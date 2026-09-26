#!/usr/bin/env python3
"""Convert a Russian frequency-list PDF into an Anki deck with TTS audio.

V11 — see CHANGELOG at bottom of file.

Expected PDF layout:

    1 - И [i] - And
    Вчера она купила фрукты и овощи.
    She bought some fruit and vegetables yesterday.

Install:
    python3 -m pip install pdfplumber genanki edge-tts

Run self-tests (no PDF, no network required):
    python3 pdf_to_anki_edge.py --self-test
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
import shutil
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Optional

__version__ = "11.0.0"

# --- Optional dependencies ---------------------------------------------------

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    import genanki
except ImportError:
    genanki = None

try:
    import edge_tts
except ImportError:
    edge_tts = None


# --- Errors ------------------------------------------------------------------

class ConversionError(Exception):
    """Base for expected, user-facing failures."""


class ParsingError(ConversionError):
    pass


class TTSError(ConversionError):
    pass


# --- Regex / constants -------------------------------------------------------

HEADER_RE = re.compile(
    r"^\s*(?P<rank>\d+)\s*[-–—]\s*"
    r"(?P<word>.+?)\s*"
    r"\[(?P<pron>.*?)\]\s*"
    r"[-–—]\s*"
    r"(?P<translation>.+?)\s*$"
)
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
LATIN_RE = re.compile(r"[A-Za-z]")

# A genuine bilingual split needs at least this many real words on EACH side.
MIN_SPLIT_WORDS = 2
# ...but for short sentences (ending in terminal punctuation on both sides),
# one word per side is unambiguous enough (e.g. "Да.   Yes.").
MIN_SPLIT_WORDS_SHORT = 1
TERMINAL_PUNCT = ".!?…"


# --- Text helpers ------------------------------------------------------------

def classify_word(word: str) -> str:
    """Classify a single token as 'ru', 'en', or 'neutral' (no letters)."""
    cyr = len(CYRILLIC_RE.findall(word))
    lat = len(LATIN_RE.findall(word))
    if cyr == 0 and lat == 0:
        return "neutral"
    return "ru" if cyr > lat else "en"


def _ends_terminal(word: str) -> bool:
    return bool(word) and word[-1] in TERMINAL_PUNCT


def split_bilingual_line(line: str) -> tuple[Optional[str], Optional[str]]:
    """Split a merged RU/EN line into (russian, english), or (None, None).

    Uses a content-aware split point: the earliest word index after which
    every remaining real word is English, with enough real words on each
    side. For short sentences ending in terminal punctuation on both sides,
    a threshold of 1 word per side is accepted (e.g. "Да.  Yes.").
    """
    words = line.split()
    if len(words) < 2:
        return None, None
    classes = [classify_word(w) for w in words]

    for i in range(1, len(words)):
        head = [c for c in classes[:i] if c != "neutral"]
        tail = [c for c in classes[i:] if c != "neutral"]
        if not head or not tail:
            continue
        if not (all(c == "ru" for c in head) and all(c == "en" for c in tail)):
            continue

        # Short-line relaxation: both halves end in terminal punctuation.
        short_ok = (
            len(head) >= MIN_SPLIT_WORDS_SHORT
            and len(tail) >= MIN_SPLIT_WORDS_SHORT
            and _ends_terminal(words[i - 1])
            and _ends_terminal(words[-1])
        )
        normal_ok = len(head) >= MIN_SPLIT_WORDS and len(tail) >= MIN_SPLIT_WORDS
        if short_ok or normal_ok:
            return " ".join(words[:i]).strip(), " ".join(words[i:]).strip()

    return None, None


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_tags(text: str) -> str:
    """Remove HTML tags and unescape entities. Robust to stray < / >."""
    if not text:
        return text
    parser = _TagStripper()
    try:
        parser.feed(text)
        parser.close()
        return parser.get_text()
    except Exception:
        # Fallback: escape-then-strip is worse than doing nothing, so just
        # remove obvious tags we know we emit.
        return re.sub(r"</?b>", "", text)


def bold_word_in_sentence(sentence: str, word: str) -> str:
    """Bold the first exact occurrence of `word` in `sentence`, HTML-safe.

    The sentence is escaped first so any `<`, `>`, `&` in source text render
    literally; only the injected `<b>` is real markup. If the word isn't
    found verbatim (inflected form), the escaped sentence is returned
    unchanged — see find_unbolded_entries().
    """
    if not sentence or not word:
        return html.escape(sentence or "")
    escaped = html.escape(sentence)
    escaped_word = html.escape(word)
    pattern = re.compile(
        r"(?<!\w)(" + re.escape(escaped_word) + r")(?!\w)",
        flags=re.IGNORECASE | re.UNICODE,
    )
    return pattern.sub(r"<b>\1</b>", escaped, count=1)


# --- PDF parsing -------------------------------------------------------------

def extract_text(pdf_path: str) -> str:
    if pdfplumber is None:
        raise ParsingError(
            "pdfplumber is not installed. Install with:\n"
            "  python3 -m pip install pdfplumber"
        )
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n\n".join(chunks)


def parse_entries(raw_text: str, debug: bool = False) -> list[dict]:
    entries: list[dict] = []
    current: Optional[dict] = None

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = HEADER_RE.match(line)
        if match:
            if current:
                entries.append(current)
            current = {
                "rank": match.group("rank"),
                "word": match.group("word").strip(),
                "pronunciation": match.group("pron").strip(),
                "translation": match.group("translation").strip(),
                "example_ru": "",
                "example_en": "",
            }
            continue

        if current is None:
            continue

        cyr_count = len(CYRILLIC_RE.findall(line))
        lat_count = len(LATIN_RE.findall(line))

        translation_unbalanced = (
            current["translation"].count("(") > current["translation"].count(")")
        )
        translation_still_incomplete = (
            not current["example_ru"] and lat_count > 0 and lat_count >= cyr_count
        )

        if translation_unbalanced:
            current["translation"] = (current["translation"] + " " + line).strip()
            if debug:
                _debug_line(line, "TRANSLATION CONTINUATION (closing paren)",
                            translation=current["translation"])
            continue

        ru_part, en_part = split_bilingual_line(line)
        if ru_part is not None:
            current["example_ru"] = (
                (current["example_ru"] + " " + ru_part).strip()
                if current["example_ru"] else ru_part
            )
            current["example_en"] = (
                (current["example_en"] + " " + en_part).strip()
                if current["example_en"] else en_part
            )
            if debug:
                _debug_line(line, "BILINGUAL (split found)",
                            example_ru=ru_part, example_en=en_part)
            continue

        if translation_still_incomplete:
            current["translation"] = (current["translation"] + " " + line).strip()
            if debug:
                _debug_line(line, "TRANSLATION CONTINUATION (no RU example yet)",
                            translation=current["translation"])
            continue

        field = "example_ru" if cyr_count > lat_count else "example_en"
        current[field] = (
            (current[field] + " " + line).strip() if current[field] else line
        )
        if debug:
            _debug_line(
                line,
                f"single-language ({'Russian' if field == 'example_ru' else 'English'})",
                **{field: line, "_counts": (cyr_count, lat_count)},
            )

    if current:
        entries.append(current)
    return entries


def _debug_line(raw: str, verdict: str, **fields) -> None:
    print(f"Raw extracted line:\n  {raw}")
    print(f"Detected: {verdict}")
    counts = fields.pop("_counts", None)
    if counts:
        print(f"  Cyrillic chars: {counts[0]}   Latin chars: {counts[1]}")
    for k, v in fields.items():
        print(f"  Parsed {k}: {v}")
    print()


# --- Entry cleaning ----------------------------------------------------------

def normalize_word(word: str) -> str:
    return word.strip().lower().rstrip(".,;:!?")


def clean_entries(raw_entries: list[dict]) -> list[dict]:
    cleaned = []
    for entry in raw_entries:
        word = normalize_word(entry["word"])
        cleaned.append({
            "rank": str(entry["rank"]),
            "word": word,
            "pronunciation": entry["pronunciation"].strip(),
            "translation": entry["translation"].strip(),
            "example_ru": bold_word_in_sentence(entry["example_ru"].strip(), word),
            "example_en": entry["example_en"].strip(),
        })
    return cleaned


def rank_int_or_none(entry: dict) -> Optional[int]:
    try:
        return int(entry["rank"])
    except (ValueError, TypeError):
        return None


def assign_sequence(entries: list[dict]) -> list[str]:
    """Set entry['seq'] (position) and entry['rank_int'] (printed rank).

    Returns a list of printed ranks where the two disagree.
    """
    mismatches = []
    for i, entry in enumerate(entries, start=1):
        entry["seq"] = i
        entry["rank_int"] = rank_int_or_none(entry)
        if entry["rank_int"] != i:
            mismatches.append(entry["rank"])
    return mismatches


def find_mixed_language_entries(entries: list[dict]) -> list[str]:
    """Flag entries where example_ru is EN-heavy OR example_en is RU-heavy.

    A Russian-dominant ExampleEN is just as suspicious as an English-dominant
    ExampleRU — both indicate the line classifier routed the wrong way.
    """
    flagged = []
    for entry in entries:
        for field, expected in (("example_ru", "ru"), ("example_en", "en")):
            text = strip_tags(entry[field])
            if not text:
                continue
            cyr = len(CYRILLIC_RE.findall(text))
            lat = len(LATIN_RE.findall(text))
            if expected == "ru" and lat > 0 and lat >= cyr:
                flagged.append(f"#{entry['rank']} ({field})")
                break
            if expected == "en" and cyr > 0 and cyr >= lat:
                flagged.append(f"#{entry['rank']} ({field})")
                break
    return flagged


def find_unbolded_entries(entries: list[dict]) -> list[str]:
    flagged = []
    for entry in entries:
        if entry["word"] and entry["example_ru"] and "<b>" not in entry["example_ru"]:
            flagged.append(entry["rank"])
    return flagged


def select_by_rank_range(
    entries: list[dict], start: Optional[int], end: Optional[int]
) -> tuple[list[dict], int, int]:
    numeric = [e["rank_int"] for e in entries if e["rank_int"] is not None]
    if not numeric:
        raise ParsingError("No entries have a usable numeric rank; can't apply --start/--end.")
    min_rank, max_rank = min(numeric), max(numeric)
    lo = start if start is not None else min_rank
    hi = end if end is not None else max_rank

    selected = [
        e for e in entries if e["rank_int"] is not None and lo <= e["rank_int"] <= hi
    ]

    print(f"Requested range: {lo}-{hi}")
    print(f"Available entries: {min_rank}-{max_rank}")
    if selected:
        sel_lo = min(e["rank_int"] for e in selected)
        sel_hi = max(e["rank_int"] for e in selected)
        print(f"Selected entries: {sel_lo}-{sel_hi} "
              f"({len(selected)} entries, {len(selected) * 2} cards)")
    else:
        print("Selected entries: none (requested range doesn't overlap the available data)")
    return selected, lo, hi


# --- Preview deck ------------------------------------------------------------

PREVIEW_ENTRIES = [
    {"rank": "1", "word": "И", "pronunciation": "i", "translation": "And",
     "example_ru": "Вчера она купила фрукты и овощи.",
     "example_en": "She bought some fruit and vegetables yesterday."},
    {"rank": "2", "word": "В", "pronunciation": "v", "translation": "In, on, at",
     "example_ru": "Маленький котёнок спрятался в подвале.",
     "example_en": "A little kitten hid in the basement."},
    {"rank": "3", "word": "Не", "pronunciation": "nje", "translation": "Not, no",
     "example_ru": "Я не люблю дождливую погоду.",
     "example_en": "I do not like rainy weather."},
    {"rank": "4", "word": "Он", "pronunciation": "on", "translation": "He",
     "example_ru": "Он очень хороший друг.",
     "example_en": "He is a very good friend."},
    {"rank": "5", "word": "На", "pronunciation": "na", "translation": "On, at",
     "example_ru": "Книга лежит на столе.",
     "example_en": "The book is lying on the table."},
]


# --- TTS backends ------------------------------------------------------------

RHVOICE_VOICES = [
    "Aleksandr-hq", "Aleksandr", "Anna", "Arina", "Artemiy", "Elena",
    "Evgeniy-Rus", "Irina", "Mikhail", "Pavel", "Tatiana", "Victoria",
    "Vitaliy", "Yuriy",
]

ESPEAK_BASE_WPM = 140


def speed_to_edge_rate(speed_pct: int) -> str:
    return f"{speed_pct - 100:+d}%"


def speed_to_rhvoice_rate(speed_pct: int) -> int:
    return speed_pct


def speed_to_espeak_wpm(speed_pct: int) -> int:
    return max(10, round(ESPEAK_BASE_WPM * speed_pct / 100))


def pick_tts_engine(requested: str) -> str:
    have_edge = edge_tts is not None
    have_rhvoice = shutil.which("RHVoice-test") is not None
    have_espeak = shutil.which("espeak-ng") is not None

    if requested == "edge":
        if not have_edge:
            raise TTSError("edge-tts is not installed. Install with:\n"
                           "  python3 -m pip install edge-tts")
        return "edge"
    if requested == "rhvoice":
        if not have_rhvoice:
            raise TTSError("RHVoice-test is not installed. On Linux Mint/Ubuntu:\n"
                           "  sudo apt install rhvoice rhvoice-russian ffmpeg")
        return "rhvoice"
    if requested == "espeak":
        if not have_espeak:
            raise TTSError("espeak-ng is not installed. On Linux Mint/Ubuntu:\n"
                           "  sudo apt install espeak-ng ffmpeg")
        return "espeak"

    if have_edge:
        return "edge"
    if have_rhvoice:
        return "rhvoice"
    if have_espeak:
        return "espeak"
    raise TTSError("No TTS engine is available. Install edge-tts, RHVoice, or eSpeak NG.")


def check_audio_dependencies(engine: str) -> None:
    if engine != "edge" and shutil.which("ffmpeg") is None:
        raise TTSError("ffmpeg is required for RHVoice/eSpeak MP3 conversion.\n"
                       "On Linux Mint/Ubuntu:\n  sudo apt install ffmpeg")


def resolve_voice(engine: str, requested_voice: Optional[str]) -> str:
    if engine == "edge":
        return requested_voice or "ru-RU-DmitryNeural"
    if engine == "rhvoice":
        voice = requested_voice or "Anna"
        match = next((v for v in RHVOICE_VOICES if v.lower() == voice.lower()), None)
        if match is None:
            raise TTSError(f"Unknown RHVoice voice '{voice}'. Options: "
                           + ", ".join(RHVOICE_VOICES))
        return match
    return requested_voice or "ru"


def resolve_speeds(args: argparse.Namespace, engine: str) -> tuple:
    """Return (normal_rate, slow_rate) in the engine's native unit.

    --speed / --slow-speed, if given, override the engine-specific flags for
    this run. Clamps are applied consistently and announced.
    """
    if args.speed is not None:
        print(f"Note: --speed {args.speed} overrides the engine-specific rate flag for this run.")
        if engine == "edge":
            normal = speed_to_edge_rate(args.speed)
        elif engine == "rhvoice":
            normal = speed_to_rhvoice_rate(args.speed)
        else:
            normal = speed_to_espeak_wpm(args.speed)
    else:
        if engine == "edge":
            normal = args.edge_rate
        elif engine == "rhvoice":
            normal = args.rhvoice_rate
        else:
            normal = args.tts_speed

    if args.slow_speed is not None:
        print(f"Note: --slow-speed {args.slow_speed} overrides the engine-specific slow-rate behavior for this run.")
        if engine == "edge":
            slow = speed_to_edge_rate(args.slow_speed)
        elif engine == "rhvoice":
            slow = speed_to_rhvoice_rate(args.slow_speed)
        else:
            slow = speed_to_espeak_wpm(args.slow_speed)
    else:
        if engine == "edge":
            slow = args.slow_rate
        elif engine == "rhvoice":
            base = args.rhvoice_rate
            slow = max(10, base // 2)
            if slow == base:
                print(f"Note: RHVoice slow rate clamped to {slow}% (was {base // 2}%).")
        else:
            base_wpm = args.tts_speed
            slow = max(40, base_wpm // 2)
            if slow >= base_wpm:
                # Only happens at pathologically low --tts-speed; keep slow
                # strictly slower than normal by falling back to normal - 10.
                slow = max(10, base_wpm - 10)
                print(f"Note: eSpeak slow rate clamped to {slow} wpm "
                      f"(was {base_wpm // 2}, which is not slower than normal).")

    return normal, slow


def _wav_to_mp3(wav_path: Path, out_mp3_path: Path, bitrate: str = "96k") -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(out_mp3_path)],
        capture_output=True, text=True,
    )
    return (result.returncode == 0
            and out_mp3_path.exists()
            and out_mp3_path.stat().st_size > 0)


def synthesize_text_audio_edge(
    text: str, out_mp3_path: Path, voice: str,
    rate: str = "+0%", pitch: str = "+0Hz",
    retries: int = 3, retry_delay: float = 1.5,
) -> bool:
    """Retries the intermittent "No audio received" edge-tts error."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            comm = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
            comm.save_sync(str(out_mp3_path))
            if out_mp3_path.exists() and out_mp3_path.stat().st_size > 0:
                return True
            last_exc = RuntimeError("no audio received (empty file)")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        out_mp3_path.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(retry_delay)
    print(f"Edge TTS error for {text!r} after {retries} attempts: {last_exc}",
          file=sys.stderr)
    return False


def synthesize_text_audio_rhvoice(
    text: str, out_mp3_path: Path, voice: str,
    rate: int = 100, pitch: int = 100, mp3_bitrate: str = "96k",
) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False,
                                     encoding="utf-8") as tmp_txt:
        tmp_txt.write(text)
        txt_path = Path(tmp_txt.name)
    wav_path = txt_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            ["RHVoice-test", "-p", voice, "-R", "24000",
             "-r", str(rate), "-t", str(pitch),
             "-i", str(txt_path), "-o", str(wav_path)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            return False
        return _wav_to_mp3(wav_path, out_mp3_path, mp3_bitrate)
    except subprocess.TimeoutExpired:
        return False
    finally:
        txt_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


def synthesize_text_audio_espeak(
    text: str, out_mp3_path: Path, voice: str, speed: int, mp3_bitrate: str = "96k",
) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = Path(tmp_wav.name)
    try:
        result = subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(wav_path), text],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            return False
        return _wav_to_mp3(wav_path, out_mp3_path, mp3_bitrate)
    finally:
        wav_path.unlink(missing_ok=True)


def synthesize_text_audio(
    text: str, out_mp3_path: Path, engine: str, voice: str, rate_value,
    edge_pitch: str, rhvoice_pitch: int, mp3_bitrate: str,
    edge_retries: int = 3, edge_retry_delay: float = 1.5,
) -> bool:
    if engine == "edge":
        return synthesize_text_audio_edge(
            text, out_mp3_path, voice=voice, rate=rate_value, pitch=edge_pitch,
            retries=edge_retries, retry_delay=edge_retry_delay,
        )
    if engine == "rhvoice":
        return synthesize_text_audio_rhvoice(
            text, out_mp3_path, voice=voice, rate=rate_value,
            pitch=rhvoice_pitch, mp3_bitrate=mp3_bitrate,
        )
    return synthesize_text_audio_espeak(
        text, out_mp3_path, voice=voice, speed=rate_value, mp3_bitrate=mp3_bitrate,
    )


# --- Caching + orchestration -------------------------------------------------

def slugify(text: str, max_len: int = 24) -> str:
    text = re.sub(r"[^\w]+", "_", text.strip().lower(), flags=re.UNICODE).strip("_")
    return text[:max_len] or "x"


def pitch_repr_for_cache(engine: str, edge_pitch: str, rhvoice_pitch: int) -> str:
    if engine == "edge":
        return edge_pitch
    if engine == "rhvoice":
        return str(rhvoice_pitch)
    return "na"


def cache_key_for(engine: str, voice: str, rate_value, pitch_value: str, text: str) -> str:
    raw = f"{engine}|{voice}|{rate_value}|{pitch_value}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def add_audio_to_entries(
    entries: list[dict], cache_dir: Path, engine: str, voice: str,
    normal_rate, slow_rate,
    edge_pitch: str, rhvoice_pitch: int, mp3_bitrate: str,
    edge_retries: int = 3, edge_retry_delay: float = 1.5,
    use_cache: bool = True,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Attach audio tags to entries.

    `progress(index, total, word)` is called once per entry, if provided.
    Returns (media_file_paths, failures).
    """
    media_files: list[str] = []
    failures: list[tuple[str, str, str]] = []
    cache_hits = 0
    cache_misses = 0
    pitch_repr = pitch_repr_for_cache(engine, edge_pitch, rhvoice_pitch)

    def get_or_make(clip_type: str, text: str, rate_value) -> Optional[Path]:
        nonlocal cache_hits, cache_misses
        key = cache_key_for(engine, voice, rate_value, pitch_repr, text)
        clip_dir = cache_dir / clip_type
        clip_dir.mkdir(parents=True, exist_ok=True)
        path = clip_dir / f"{slugify(text)}_{key}.mp3"

        if use_cache and path.exists() and path.stat().st_size > 0:
            cache_hits += 1
            return path

        ok = synthesize_text_audio(
            text, path, engine=engine, voice=voice, rate_value=rate_value,
            edge_pitch=edge_pitch, rhvoice_pitch=rhvoice_pitch,
            mp3_bitrate=mp3_bitrate,
            edge_retries=edge_retries, edge_retry_delay=edge_retry_delay,
        )
        cache_misses += 1
        return path if ok else None

    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        if progress:
            progress(index, total, entry["word"])
        text_word = entry["word"]
        text_sentence = strip_tags(entry["example_ru"]).strip()

        if text_word:
            path = get_or_make("word", text_word, normal_rate)
            if path:
                entry["audio_tag"] = f"[sound:{path.name}]"
                media_files.append(str(path))
            else:
                entry["audio_tag"] = ""
                failures.append((entry["rank"], entry["word"], "word"))
        else:
            entry["audio_tag"] = ""

        if text_sentence:
            path = get_or_make("example", text_sentence, normal_rate)
            if path:
                entry["example_audio_tag"] = f"[sound:{path.name}]"
                media_files.append(str(path))
            else:
                entry["example_audio_tag"] = ""
                failures.append((entry["rank"], entry["word"], "sentence"))

            slow_path = get_or_make("example_slow", text_sentence, slow_rate)
            if slow_path:
                entry["example_audio_slow_tag"] = f"[sound:{slow_path.name}]"
                media_files.append(str(slow_path))
            else:
                entry["example_audio_slow_tag"] = ""
                failures.append((entry["rank"], entry["word"], "sentence_slow"))
        else:
            entry["example_audio_tag"] = ""
            entry["example_audio_slow_tag"] = ""

    print(f"  Cache: {cache_hits} reused, {cache_misses} generated")
    media_files = list(dict.fromkeys(media_files))
    return media_files, failures


def write_failed_log(path: Path, failures: list[tuple[str, str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for rank, word, clip_type in failures:
            f.write(f"#{rank} {word} ({clip_type})\n")


# --- Anki deck construction --------------------------------------------------

# NOTE: bumping MODEL_ID creates a NEW note type in Anki and orphans existing
# cards on re-import. Only bump when templates/fields actually change.
MODEL_ID = 1607392409
DECK_ID = 2059852317

CARD_CSS = """
.card {
    font-family: Arial, sans-serif;
    font-size: 20px;
    text-align: center;
    color: black;
    background-color: white;
}
.pron { font-size: 15px; color: #555; margin-top: 6px; }
.example {
    font-size: 15px;
    margin-top: 14px;
    text-align: left;
    display: inline-block;
}
.example .en { color: #555; }
.example b { font-weight: 700; }
.rank { font-size: 12px; color: #999; margin-top: 10px; }
.audio { margin-top: 8px; }
.audio-row { margin-top: 4px; }
"""

EXAMPLE_BLOCK = """{{#ExampleRU}}
<div class="example">
{{ExampleRU}}<br>
{{#ExampleAudioSlow}}
<div class="audio-row">🐢 {{ExampleAudioSlow}}</div>
{{/ExampleAudioSlow}}
{{#ExampleAudio}}
<div class="audio-row">🐇 {{ExampleAudio}}</div>
{{/ExampleAudio}}
<span class="en">{{ExampleEN}}</span>
</div>
{{/ExampleRU}}"""

RANK_LINE = '<div class="rank">#{{Rank}} most frequent</div>'

RU_EN_QFMT = """
<div>{{Word}}</div>
<div class="audio">{{Audio}}</div>
"""

RU_EN_AFMT = (
    "{{FrontSide}}\n"
    '<hr id="answer">\n'
    '<div class="pron">Russian Pronunciation: [{{Pronunciation}}]</div>\n'
    "<div>{{Translation}}</div>\n"
    + EXAMPLE_BLOCK + "\n"
    + RANK_LINE + "\n"
)

EN_RU_QFMT = """
<div>{{Translation}}</div>
"""

EN_RU_AFMT = (
    "{{FrontSide}}\n"
    '<hr id="answer">\n'
    "<div>{{Word}}</div>\n"
    '<div class="pron">Russian Pronunciation: [{{Pronunciation}}]</div>\n'
    '<div class="audio">{{Audio}}</div>\n'
    + EXAMPLE_BLOCK + "\n"
    + RANK_LINE + "\n"
)


def build_model() -> "genanki.Model":
    if genanki is None:
        raise ConversionError(
            "genanki is not installed. Install with:\n"
            "  python3 -m pip install genanki"
        )
    return genanki.Model(
        MODEL_ID,
        "Russian Frequency (RU<->EN, Edge TTS)",
        fields=[
            {"name": "Word"}, {"name": "Pronunciation"}, {"name": "Translation"},
            {"name": "ExampleRU"}, {"name": "ExampleEN"}, {"name": "Rank"},
            {"name": "Audio"}, {"name": "ExampleAudio"}, {"name": "ExampleAudioSlow"},
        ],
        templates=[
            {"name": "Russian -> English", "qfmt": RU_EN_QFMT, "afmt": RU_EN_AFMT},
            {"name": "English -> Russian", "qfmt": EN_RU_QFMT, "afmt": EN_RU_AFMT},
        ],
        css=CARD_CSS,
    )


def build_deck(entries: list[dict], deck_name: str) -> "genanki.Deck":
    model = build_model()
    deck = genanki.Deck(DECK_ID, deck_name)
    for entry in entries:
        # Stable GUID: same (rank, word) re-imports as an UPDATE, not