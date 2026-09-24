#!/usr/bin/env python3
"""Convert a Russian frequency-list PDF into an Anki deck with TTS audio.

The expected PDF layout is:

    1 - И [i] - And
    Вчера она купила фрукты и овощи.
    She bought some fruit and vegetables yesterday.

By default, audio is generated online with Microsoft Edge TTS using the
Russian male voice ``ru-RU-DmitryNeural``.  Each note produces two Anki cards:
Russian -> English and English -> Russian, and each includes both a normal-
speed and a slowed-down clip of the Russian example sentence.

Install:
    python3 -m pip install pdfplumber genanki edge-tts

Edge TTS needs an internet connection and sends the Russian text to
Microsoft's speech service.  RHVoice and eSpeak remain available as optional
offline backends, but require their own system packages and ffmpeg.

Edge TTS occasionally raises a "No audio was received" error - this is a
known, long-standing issue in the edge-tts library itself during sustained
use (see rany2/edge-tts on GitHub), not something wrong with your input.
This script automatically retries each clip a few times before giving up;
tune with --edge-retries / --edge-retry-delay.

Generated audio is cached under --cache-dir (default: ./tts_cache) and
reused automatically on later runs - including overlapping --start/--end
ranges - as long as the text, voice, engine, rate, and pitch all match.
"""

import argparse
import csv
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit(
        "Missing dependency: pdfplumber\n"
        "Install dependencies with:\n"
        "  python3 -m pip install pdfplumber genanki edge-tts"
    )

try:
    import genanki
except ImportError:
    sys.exit(
        "Missing dependency: genanki\n"
        "Install dependencies with:\n"
        "  python3 -m pip install pdfplumber genanki edge-tts"
    )

try:
    import edge_tts
except ImportError:
    edge_tts = None


HEADER_RE = re.compile(
    r"^\s*(?P<rank>\d+)\s*[-–—]\s*"
    r"(?P<word>.+?)\s*"
    r"\[(?P<pron>.*?)\]\s*"
    r"[-–—]\s*"
    r"(?P<translation>.+?)\s*$"
)
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
LATIN_RE = re.compile(r"[A-Za-z]")

# A genuine bilingual split needs at least this many real (non-neutral)
# words on EACH side. Without this, a single stray Latin abbreviation or
# name at the end of an otherwise-Russian sentence (which happens - see the
# parsing fix discussion) would look like a one-word "English tail" and
# trigger a false split.
MIN_SPLIT_WORDS = 2


def classify_word(word: str) -> str:
    """Classifies a single word as 'ru', 'en', or 'neutral' (no letters at
    all - pure punctuation/numbers, which carries no evidence either way)."""
    cyr = len(CYRILLIC_RE.findall(word))
    lat = len(LATIN_RE.findall(word))
    if cyr == 0 and lat == 0:
        return "neutral"
    return "ru" if cyr > lat else "en"


def split_bilingual_line(line: str) -> tuple[str, str] | tuple[None, None]:
    """
    Some PDF pages lay the Russian example and its English translation out
    as two side-by-side columns. pdfplumber's extract_text() doesn't always
    preserve that as two separate lines - it can merge them into ONE
    extracted line, e.g.:

        "Я могу зайти завтра, если хочешь.    I can drop by tomorrow if you want."

    This looks for a content-based split point (not a fixed character
    position, and not "first/last space"): the earliest word index after
    which every remaining real word is English, with at least
    MIN_SPLIT_WORDS real words on both sides. If no such point exists, this
    is an ordinary single-language line and (None, None) is returned so the
    caller falls back to its normal whole-line classification.
    """
    words = line.split()
    classes = [classify_word(w) for w in words]

    for i in range(len(words)):
        head = [c for c in classes[:i] if c != "neutral"]
        tail = [c for c in classes[i:] if c != "neutral"]
        if (
            len(head) >= MIN_SPLIT_WORDS
            and len(tail) >= MIN_SPLIT_WORDS
            and all(c == "ru" for c in head)
            and all(c == "en" for c in tail)
        ):
            ru_part = " ".join(words[:i]).strip()
            en_part = " ".join(words[i:]).strip()
            return ru_part, en_part

    return None, None


def parse_entries(raw_text: str, debug: bool = False) -> list[dict]:
    entries = []
    current = None

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

        # First: is this ONE line that actually contains BOTH a Russian
        # example and its English translation side by side (see
        # split_bilingual_line)? If so, route each part to its own field
        # directly instead of forcing the whole line into one field.
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
                print(f"Raw extracted line:\n  {line}")
                print(f"Detected: BILINGUAL (split found)")
                print(f"  Parsed ExampleRU: {ru_part}")
                print(f"  Parsed ExampleEN: {en_part}\n")
            continue

        # Otherwise: the existing whole-line vote, unchanged from before.
        # Classify by which script DOMINATES the line, not merely which is
        # present. A line like "an obvious fact (A shorter form of 'же')" has
        # one Cyrillic word but is overwhelmingly English commentary - the old
        # "any Cyrillic char present" check misfiled lines like that as
        # Russian, corrupting example_ru with English text glued onto it
        # (and then breaking TTS on that entry). Comparing letter counts fixes
        # this for the vast majority of real dictionary/footnote-style noise.
        cyr_count = len(CYRILLIC_RE.findall(line))
        lat_count = len(LATIN_RE.findall(line))
        field = "example_ru" if cyr_count > lat_count else "example_en"
        current[field] = (
            (current[field] + " " + line).strip() if current[field] else line
        )

        if debug:
            print(f"Raw extracted line:\n  {line}")
            print(f"Detected: single-language ({'Russian' if field == 'example_ru' else 'English'})")
            print(f"  Cyrillic chars: {cyr_count}   Latin chars: {lat_count}")
            print(f"  Parsed {field}: {line}\n")

    if current:
        entries.append(current)
    return entries


# The first four entries come directly from the supplied example image.
# Entry 5 is included only so --preview creates a five-word test deck.
PREVIEW_ENTRIES = [
    {
        "rank": "1",
        "word": "И",
        "pronunciation": "i",
        "translation": "And",
        "example_ru": "Вчера она купила фрукты и овощи.",
        "example_en": "She bought some fruit and vegetables yesterday.",
    },
    {
        "rank": "2",
        "word": "В",
        "pronunciation": "v",
        "translation": "In, on, at",
        "example_ru": "Маленький котёнок спрятался в подвале.",
        "example_en": "A little kitten hid in the basement.",
    },
    {
        "rank": "3",
        "word": "Не",
        "pronunciation": "nje",
        "translation": "Not, no",
        "example_ru": "Я не люблю дождливую погоду.",
        "example_en": "I do not like rainy weather.",
    },
    {
        "rank": "4",
        "word": "Он",
        "pronunciation": "on",
        "translation": "He",
        "example_ru": "Он очень хороший друг.",
        "example_en": "He is a very good friend.",
    },
    {
        "rank": "5",
        "word": "На",
        "pronunciation": "na",
        "translation": "On, at",
        "example_ru": "Книга лежит на столе.",
        "example_en": "The book is lying on the table.",
    },
]


# --- PDF parsing -------------------------------------------------------------

def extract_text(pdf_path: str) -> str:
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n\n".join(chunks)


def rank_int_or_none(entry: dict) -> int | None:
    try:
        return int(entry["rank"])
    except (ValueError, TypeError):
        return None


def assign_sequence(entries: list[dict]) -> list[str]:
    """
    Assigns entry["seq"] = 1, 2, 3... in PDF appearance order, and
    entry["rank_int"] = the parsed rank as an int (or None if it isn't
    numeric). These are DELIBERATELY different concepts:

      - seq: where the entry actually landed in the parsed list.
      - rank_int: the number printed next to it in the PDF.

    They normally agree. When they don't - a header split across two lines,
    a duplicate rank, an OCR glitch - that's a sign something upstream may
    have silently dropped or misread an entry. This function only reports
    that disagreement; --start/--end filter on rank_int (see
    select_by_rank_range), not on seq, so a printed range like 501-1000
    always means exactly that, with a gap left visible if something's
    missing rather than everything after it silently shifting.
    """
    mismatches = []
    for i, entry in enumerate(entries, start=1):
        entry["seq"] = i
        entry["rank_int"] = rank_int_or_none(entry)
        if entry["rank_int"] != i:
            mismatches.append(entry["rank"])
    return mismatches


def normalize_word(word: str) -> str:
    """Use dictionary-style lowercase for the vocabulary item."""
    return word.strip().lower()


def bold_word_in_sentence(sentence: str, word: str) -> str:
    """Bold only the first exact occurrence of the card's target word.
    If the word isn't found verbatim (e.g. an inflected form like "руку"
    for the dictionary form "рука"), the sentence is returned unchanged -
    see find_unbolded_entries() for how that gets surfaced instead of
    silently going unnoticed."""
    if not sentence or not word:
        return sentence
    pattern = re.compile(
        r"(?<!\w)(" + re.escape(word) + r")(?!\w)",
        flags=re.IGNORECASE | re.UNICODE,
    )
    return pattern.sub(r"<b>\1</b>", sentence, count=1)


def strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def clean_entries(raw_entries: list[dict]) -> list[dict]:
    cleaned = []
    for entry in raw_entries:
        word = normalize_word(entry["word"])
        cleaned.append(
            {
                "rank": str(entry["rank"]),
                "word": word,
                "pronunciation": entry["pronunciation"].strip(),
                "translation": entry["translation"].strip(),
                "example_ru": bold_word_in_sentence(
                    entry["example_ru"].strip(), word
                ),
                "example_en": entry["example_en"].strip(),
            }
        )
    return cleaned


def find_mixed_language_entries(entries: list[dict]) -> list[str]:
    """
    Flags entries whose example_ru still looks suspiciously English-heavy
    after cleaning (e.g. leftover dictionary/footnote text that the line
    classifier couldn't fully separate). These are worth a manual look in
    the source PDF - passing them to TTS as-is tends to fail or produce
    garbled audio.
    """
    flagged = []
    for entry in entries:
        text = strip_tags(entry["example_ru"])
        if not text:
            continue
        cyr = len(CYRILLIC_RE.findall(text))
        lat = len(LATIN_RE.findall(text))
        if lat > 0 and lat >= cyr:
            flagged.append(entry["rank"])
    return flagged


def find_unbolded_entries(entries: list[dict]) -> list[str]:
    """
    bold_word_in_sentence() silently leaves example_ru unchanged if the
    exact word form isn't found (typically an inflected form, e.g. the
    dictionary form "рука" vs. "руку" in the example sentence). This does
    NOT attempt any morphological matching - that would need real Russian
    stemming/lemmatization to do safely, and a naive prefix match risks
    false positives on short words. It just makes the miss visible instead
    of silently shipping a card with no bolding and no way to notice.
    """
    flagged = []
    for entry in entries:
        if entry["word"] and entry["example_ru"] and "<b>" not in entry["example_ru"]:
            flagged.append(entry["rank"])
    return flagged


def select_by_rank_range(
    entries: list[dict], start: int | None, end: int | None
) -> tuple[list[dict], int, int]:
    """
    Filters to entries whose PRINTED rank (not seq/position) falls within
    [start, end] inclusive. Never raises just because the range exceeds
    what's available - reports requested vs. available vs. actually
    selected, and clamps. Returns (selected_entries, requested_lo, requested_hi).
    """
    numeric = [e["rank_int"] for e in entries if e["rank_int"] is not None]
    if not numeric:
        sys.exit("No entries have a usable numeric rank; can't apply --start/--end.")
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
        print(
            f"Selected entries: {sel_lo}-{sel_hi} "
            f"({len(selected)} entries, {len(selected) * 2} cards)"
        )
    else:
        print("Selected entries: none (requested range doesn't overlap the available data)")

    return selected, lo, hi


# --- Audio generation --------------------------------------------------------

RHVOICE_VOICES = [
    "Aleksandr-hq",
    "Aleksandr",
    "Anna",
    "Arina",
    "Artemiy",
    "Elena",
    "Evgeniy-Rus",
    "Irina",
    "Mikhail",
    "Pavel",
    "Tatiana",
    "Victoria",
    "Vitaliy",
    "Yuriy",
]

ESPEAK_BASE_WPM = 140  # what --speed=100 maps to for the espeak backend


def speed_to_edge_rate(speed_pct: int) -> str:
    """Edge's rate is a DELTA from normal ('-25%' = 75% speed), not a direct
    percent-of-normal, so: speed_pct=100 -> '+0%', speed_pct=75 -> '-25%'."""
    return f"{speed_pct - 100:+d}%"


def speed_to_rhvoice_rate(speed_pct: int) -> int:
    """RHVoice's -r IS already percent-of-normal - direct pass-through."""
    return speed_pct


def speed_to_espeak_wpm(speed_pct: int) -> int:
    return max(10, round(ESPEAK_BASE_WPM * speed_pct / 100))


def pick_tts_engine(requested: str) -> str:
    have_edge = edge_tts is not None
    have_rhvoice = shutil.which("RHVoice-test") is not None
    have_espeak = shutil.which("espeak-ng") is not None

    if requested == "edge":
        if not have_edge:
            sys.exit(
                "edge-tts is not installed. Install it with:\n"
                "  python3 -m pip install edge-tts"
            )
        return "edge"
    if requested == "rhvoice":
        if not have_rhvoice:
            sys.exit(
                "RHVoice-test is not installed. On Linux Mint/Ubuntu:\n"
                "  sudo apt install rhvoice rhvoice-russian ffmpeg"
            )
        return "rhvoice"
    if requested == "espeak":
        if not have_espeak:
            sys.exit(
                "espeak-ng is not installed. On Linux Mint/Ubuntu:\n"
                "  sudo apt install espeak-ng ffmpeg"
            )
        return "espeak"

    # Auto favors Edge for quality, then uses an offline backend if Edge is
    # unavailable. Select rhvoice/espeak explicitly when privacy is preferred.
    if have_edge:
        return "edge"
    if have_rhvoice:
        return "rhvoice"
    if have_espeak:
        return "espeak"
    sys.exit("No TTS engine is available. Install edge-tts, RHVoice, or eSpeak NG.")


def check_audio_dependencies(engine: str) -> None:
    if engine != "edge" and shutil.which("ffmpeg") is None:
        sys.exit(
            "ffmpeg is required for RHVoice/eSpeak MP3 conversion.\n"
            "On Linux Mint/Ubuntu:\n"
            "  sudo apt install ffmpeg"
        )


def resolve_voice(engine: str, requested_voice: str | None) -> str:
    if engine == "edge":
        return requested_voice or "ru-RU-DmitryNeural"
    if engine == "rhvoice":
        voice = requested_voice or "Anna"
        match = next((v for v in RHVOICE_VOICES if v.lower() == voice.lower()), None)
        if match is None:
            sys.exit(
                f"Unknown RHVoice voice '{voice}'. Options: "
                + ", ".join(RHVOICE_VOICES)
            )
        return match
    return requested_voice or "ru"


def resolve_speeds(args: argparse.Namespace, engine: str) -> tuple:
    """
    Returns (normal_rate, slow_rate) in whatever unit `engine` expects
    (edge: a rate string like '+0%'; rhvoice: an int percent; espeak: an
    int words/minute).

    Backward compatibility: if --speed/--slow-speed are NOT given, this
    reproduces the exact previous default behavior using each engine's own
    legacy flag (--edge-rate/--rhvoice-rate/--tts-speed, and --slow-rate or
    half of the normal rate for the offline engines). --speed/--slow-speed
    are opt-in convenience flags that, only if passed, override those
    legacy flags for this run - each override is announced so it's never
    silently ambiguous which one took effect.
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
            slow = max(10, args.rhvoice_rate // 2)
        else:
            slow = max(40, args.tts_speed // 2)

    return normal, slow


def _wav_to_mp3(wav_path: Path, out_mp3_path: Path, bitrate: str = "96k") -> bool:
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame", "-b:a", bitrate,
            str(out_mp3_path),
        ],
        capture_output=True, text=True,
    )
    return (
        result.returncode == 0
        and out_mp3_path.exists()
        and out_mp3_path.stat().st_size > 0
    )


def synthesize_text_audio_edge(
    text: str, out_mp3_path: Path, voice: str,
    rate: str = "+0%", pitch: str = "+0Hz",
    retries: int = 3, retry_delay: float = 1.5,
) -> bool:
    """
    edge-tts's "No audio was received" error is a well-documented, long-
    standing issue in the library itself (see rany2/edge-tts on GitHub) -
    it's an intermittent hiccup talking to Microsoft's TTS websocket during
    sustained use, not something wrong with the input text. Retrying with a
    short pause almost always succeeds on the 2nd or 3rd attempt.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
            communicator.save_sync(str(out_mp3_path))
            if out_mp3_path.exists() and out_mp3_path.stat().st_size > 0:
                return True
            last_exc = RuntimeError("no audio received (empty file)")
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last_exc = exc

        out_mp3_path.unlink(missing_ok=True)
        if attempt < retries:
            time.sleep(retry_delay)

    print(f"Edge TTS error for {text!r} after {retries} attempts: {last_exc}", file=sys.stderr)
    return False


def synthesize_text_audio_rhvoice(
    text: str, out_mp3_path: Path, voice: str,
    rate: int = 100, pitch: int = 100, mp3_bitrate: str = "96k",
) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as tmp_txt:
        tmp_txt.write(text)
        txt_path = Path(tmp_txt.name)
    wav_path = txt_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            ["RHVoice-test", "-p", voice, "-R", "24000", "-r", str(rate), "-t", str(pitch),
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
    """rate_value is already in whatever unit `engine` expects - see
    resolve_speeds()."""
    if engine == "edge":
        return synthesize_text_audio_edge(
            text, out_mp3_path, voice=voice, rate=rate_value, pitch=edge_pitch,
            retries=edge_retries, retry_delay=edge_retry_delay,
        )
    if engine == "rhvoice":
        return synthesize_text_audio_rhvoice(
            text, out_mp3_path, voice=voice, rate=rate_value, pitch=rhvoice_pitch,
            mp3_bitrate=mp3_bitrate,
        )
    return synthesize_text_audio_espeak(
        text, out_mp3_path, voice=voice, speed=rate_value, mp3_bitrate=mp3_bitrate,
    )


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
    """
    Hashes every input that actually affects the resulting audio. If ANY of
    engine/voice/rate/pitch/text differ from a previous run, this produces a
    different key -> guaranteed cache miss -> regenerate. Only an exact
    match on all five reuses a cached clip, so there's no risk of playing
    back the wrong word's audio just because a rank or filename happened to
    line up.
    """
    raw = f"{engine}|{voice}|{rate_value}|{pitch_value}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def add_audio_to_entries(
    entries: list[dict], cache_dir: Path, engine: str, voice: str,
    normal_rate, slow_rate,
    edge_pitch: str, rhvoice_pitch: int, mp3_bitrate: str,
    edge_retries: int = 3, edge_retry_delay: float = 1.5, use_cache: bool = True,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """
    Returns (media_file_paths, failures). failures is a list of
    (rank, word, clip_type) tuples for anything that couldn't be
    synthesized, so the caller can report and log them without the whole
    run failing over one bad clip.
    """
    media_files = []
    failures: list[tuple[str, str, str]] = []
    cache_hits = 0
    cache_misses = 0
    pitch_repr = pitch_repr_for_cache(engine, edge_pitch, rhvoice_pitch)

    def get_or_make(clip_type: str, text: str, rate_value) -> Path | None:
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
            edge_pitch=edge_pitch, rhvoice_pitch=rhvoice_pitch, mp3_bitrate=mp3_bitrate,
            edge_retries=edge_retries, edge_retry_delay=edge_retry_delay,
        )
        cache_misses += 1
        return path if ok else None

    for index, entry in enumerate(entries, start=1):
        print(f"  Audio {index}/{len(entries)}: {entry['word']}")
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
    media_files = list(dict.fromkeys(media_files))  # dedupe, preserve order
    return media_files, failures


def write_failed_log(path: Path, failures: list[tuple[str, str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for rank, word, clip_type in failures:
            f.write(f"#{rank} {word} ({clip_type})\n")


# --- Anki deck construction ---------------------------------------------------

MODEL_ID = 1607392409  # bumped: card templates changed (EN->RU now matches RU->EN)
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

# Shared by both card types so they can never drift apart again (see the
# analysis: this was the actual bug that motivated this change).
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


def build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "Russian Frequency (RU<->EN, Edge TTS)",
        fields=[
            {"name": "Word"},
            {"name": "Pronunciation"},
            {"name": "Translation"},
            {"name": "ExampleRU"},
            {"name": "ExampleEN"},
            {"name": "Rank"},
            {"name": "Audio"},
            {"name": "ExampleAudio"},
            {"name": "ExampleAudioSlow"},
        ],
        templates=[
            {"name": "Russian -> English", "qfmt": RU_EN_QFMT, "afmt": RU_EN_AFMT},
            {"name": "English -> Russian", "qfmt": EN_RU_QFMT, "afmt": EN_RU_AFMT},
        ],
        css=CARD_CSS,
    )


def build_deck(entries: list[dict], deck_name: str) -> genanki.Deck:
    model = build_model()
    deck = genanki.Deck(DECK_ID, deck_name)
    for entry in entries:
        note = genanki.Note(
            model=model,
            fields=[
                entry["word"],
                entry["pronunciation"],
                entry["translation"],
                entry["example_ru"],
                entry["example_en"],
                entry["rank"],
                entry.get("audio_tag", ""),
                entry.get("example_audio_tag", ""),
                entry.get("example_audio_slow_tag", ""),
            ],
        )
        deck.add_note(note)
    return deck


def write_csv(entries: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "rank", "word", "pronunciation", "translation",
                "example_ru", "example_en",
                "audio_tag", "example_audio_tag", "example_audio_slow_tag",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(entries)


# --- CLI ----------------------------------------------------------------------

EPILOG = """
Examples
--------
First 100 words:
  python3 pdf_to_anki_edge.py words.pdf --limit 100 -o first_100.apkg

Words 501-1000 (by the PDF's own printed rank number, not just position):
  python3 pdf_to_anki_edge.py words.pdf --start 501 --end 1000 -o 501_1000.apkg

Check a range and the parsed data BEFORE running any TTS (fast, no audio/file made):
  python3 pdf_to_anki_edge.py words.pdf --start 501 --end 1000 --dry-run

Test without audio (fast, no network/TTS needed):
  python3 pdf_to_anki_edge.py words.pdf --limit 10 --no-audio -o test.apkg

Generate a CSV backup alongside the deck:
  python3 pdf_to_anki_edge.py words.pdf --csv words.csv -o words.apkg

Simple cross-engine speed control (100 = normal speed, 75 = three-quarter speed):
  python3 pdf_to_anki_edge.py words.pdf --speed 100 --slow-speed 75

Force a fresh cache-free rebuild:
  python3 pdf_to_anki_edge.py words.pdf --no-cache

Mutually exclusive:
  --limit          cannot be combined with --start/--end
  --preview        cannot be combined with a PDF path
  --speed          overrides --edge-rate / --rhvoice-rate / --tts-speed for this run
  --slow-speed     overrides --slow-rate / the auto half-rate for this run
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a Russian frequency PDF into an Anki audio deck.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", nargs="?", help="Input PDF. Not required when --preview is used.")
    parser.add_argument("--preview", action="store_true",
                         help="Build the built-in five-word preview instead of reading a PDF.")
    parser.add_argument("--limit", type=int, metavar="X",
                         help="Use only the first X parsed words (by position, not rank).")
    parser.add_argument("--start", type=int, metavar="N", default=None,
                         help="Only include words with PRINTED rank >= N (inclusive). "
                              "Combine with --end for a range, e.g. --start 501 --end 1000.")
    parser.add_argument("--end", type=int, metavar="N", default=None,
                         help="Only include words with PRINTED rank <= N (inclusive).")
    parser.add_argument("--dry-run", action="store_true",
                         help="Validate the PDF/range and print a summary, but generate no "
                              "audio and write no files.")
    parser.add_argument("-o", "--output", default="russian_deck.apkg",
                         help="Output .apkg path (default: russian_deck.apkg).")
    parser.add_argument("--deck-name", default="Russian Most Frequent Words",
                         help="Name shown inside Anki.")
    parser.add_argument("--csv", help="Optional CSV backup path.")
    parser.add_argument("--no-audio", action="store_true", help="Build cards without generating audio.")
    parser.add_argument("--tts-engine", choices=["edge", "auto", "rhvoice", "espeak"], default="edge",
                         help="TTS backend (default: edge). Edge is online.")
    parser.add_argument("--voice", help="Voice name. Edge default: ru-RU-DmitryNeural; "
                                         "RHVoice default: Anna; eSpeak default: ru.")
    parser.add_argument("--edge-rate", default="+0%", help="Edge speaking rate, e.g. -10%% or +20%% (default: +0%%).")
    parser.add_argument("--edge-pitch", default="+0Hz", help="Edge pitch, e.g. -10Hz or +10Hz (default: +0Hz).")
    parser.add_argument("--edge-retries", type=int, default=3,
                         help="Retry attempts for edge-tts's intermittent error (default: 3).")
    parser.add_argument("--edge-retry-delay", type=float, default=1.5,
                         help="Seconds between edge-tts retry attempts (default: 1.5).")
    parser.add_argument("--slow-rate", default="-50%",
                         help="Edge rate for the slow example-sentence clip (default: -50%%, half speed).")
    parser.add_argument("--tts-speed", type=int, default=140, help="eSpeak speed in words/minute (default: 140).")
    parser.add_argument("--rhvoice-rate", type=int, default=100, help="RHVoice speed, %% of normal (default: 100).")
    parser.add_argument("--rhvoice-pitch", type=int, default=100, help="RHVoice pitch, %% of normal (default: 100).")
    parser.add_argument("--mp3-bitrate", default="96k", help="mp3 bitrate for RHVoice/eSpeak output (default: 96k).")
    parser.add_argument("--speed", type=int, default=None,
                         help="Convenience: normal speaking speed as %% of default (100=normal), same units "
                              "for every engine. If given, overrides --edge-rate/--rhvoice-rate/--tts-speed "
                              "for this run only.")
    parser.add_argument("--slow-speed", type=int, default=None,
                         help="Convenience: speed for the slow example-sentence clip, %% of default. If given, "
                              "overrides --slow-rate / the automatic half-rate for this run only.")
    parser.add_argument("--cache-dir", default="tts_cache",
                         help="Where generated audio is cached and reused across runs (default: ./tts_cache).")
    parser.add_argument("--no-cache", action="store_true",
                         help="Don't reuse cached audio (still writes new clips to the cache).")
    parser.add_argument("--failed-log", default=None,
                         help="Where to write the list of failed audio clips, if any "
                              "(default: <output>.failed.txt).")
    parser.add_argument("--debug-parse", action="store_true",
                         help="Print each raw extracted line during parsing, what was detected "
                              "(single-language or a bilingual split), and the resulting field(s). "
                              "Combine with --dry-run to inspect parsing without generating audio, "
                              "and pipe through 'tail'/'grep' to focus on specific pages, e.g.: "
                              "python3 script.py words.pdf --debug-parse --dry-run | tail -100")
    args = parser.parse_args()

    if not args.preview and not args.pdf:
        parser.error("provide a PDF path or use --preview")
    if args.preview and args.pdf:
        parser.error("use either a PDF path or --preview, not both")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    if args.limit is not None and (args.start is not None or args.end is not None):
        parser.error("use either --limit or --start/--end, not both")
    if args.start is not None and args.start < 1:
        parser.error("--start must be a positive integer")
    if args.end is not None and args.end < 1:
        parser.error("--end must be a positive integer")
    if args.start is not None and args.end is not None and args.start > args.end:
        parser.error("--start must be <= --end")
    if args.speed is not None and args.speed < 10:
        parser.error("--speed must be at least 10")
    if args.slow_speed is not None and args.slow_speed < 10:
        parser.error("--slow-speed must be at least 10")
    return args


def main() -> None:
    args = parse_args()

    if args.preview:
        raw_entries = [dict(entry) for entry in PREVIEW_ENTRIES]
        if args.deck_name == "Russian Most Frequent Words":
            args.deck_name = "Russian Frequency - 5 Word Preview"
        print("Using the built-in five-word preview.")
    else:
        if not Path(args.pdf).exists():
            sys.exit(f"File not found: {args.pdf}")
        print(f"Reading {args.pdf} ...")
        raw_entries = parse_entries(extract_text(args.pdf), debug=args.debug_parse)

    entries = clean_entries(raw_entries)
    if not entries:
        sys.exit("No entries were found. Check the PDF text layout.")
    total_entries = len(entries)
    print(f"PDF entries found: {total_entries}")

    rank_mismatches = assign_sequence(entries)
    if rank_mismatches:
        print(
            "Warning: printed rank number didn't match actual position in the PDF "
            "for these entries (--start/--end still use the PRINTED rank; a gap "
            "here means one of these numbers may not select what you expect): "
            + ", ".join(rank_mismatches[:20]),
            file=sys.stderr,
        )

    requested_lo = requested_hi = None
    if args.start is not None or args.end is not None:
        entries, requested_lo, requested_hi = select_by_rank_range(entries, args.start, args.end)
        if not entries:
            sys.exit("No entries fall within the requested --start/--end range.")
        if args.deck_name == "Russian Most Frequent Words":
            lo = min(e["rank_int"] for e in entries)
            hi = max(e["rank_int"] for e in entries)
            args.deck_name = f"Russian Most Frequent Words ({lo}-{hi})"
    elif args.limit is not None:
        entries = entries[: args.limit]
        print(f"Using {len(entries)} of {total_entries} entries ({len(entries) * 2} cards).")
    else:
        print(f"Selected entries: all {total_entries} ({total_entries * 2} cards).")

    entries.sort(key=lambda e: e["seq"])  # guarantee export order == frequency order

    missing_examples = [
        entry["rank"] for entry in entries if not entry["example_ru"] or not entry["example_en"]
    ]
    if missing_examples:
        print("Warning: entries missing an example: " + ", ".join(missing_examples[:20]), file=sys.stderr)

    mixed_language = find_mixed_language_entries(entries)
    if mixed_language:
        print(
            "Warning: these entries' example_ru still looks English-heavy after "
            "parsing (likely leftover dictionary/footnote text) - worth checking "
            "manually: " + ", ".join(mixed_language[:20]),
            file=sys.stderr,
        )

    unbolded = find_unbolded_entries(entries)
    if unbolded:
        print(
            "Warning: target word not found verbatim in its example sentence "
            "(likely an inflected form) - not bolded on these cards: "
            + ", ".join(unbolded[:20]),
            file=sys.stderr,
        )

    if args.dry_run:
        print()
        print("Dry run - no audio or files were generated")
        print("-------------------------------------------")
        if requested_lo is not None:
            print(f"Requested range: {requested_lo}-{requested_hi}")
        elif args.limit is not None:
            print(f"Requested: first {args.limit} (via --limit)")
        else:
            print("Requested range: (all)")
        print(f"Selected entries: {len(entries)}")
        print()
        print(f"Missing examples: {len(missing_examples)}")
        print(f"Mixed-language examples: {len(mixed_language)}")
        print(f"Rank mismatches: {len(rank_mismatches)}")
        print(f"Unbolded target words: {len(unbolded)}")
        print()
        print(f"Cards to create: {len(entries) * 2}")
        return

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    media_files: list[str] = []
    failures: list[tuple[str, str, str]] = []
    if args.no_audio:
        for entry in entries:
            entry["audio_tag"] = ""
            entry["example_audio_tag"] = ""
            entry["example_audio_slow_tag"] = ""
    else:
        engine = pick_tts_engine(args.tts_engine)
        check_audio_dependencies(engine)
        voice = resolve_voice(engine, args.voice)
        normal_rate, slow_rate = resolve_speeds(args, engine)
        print(f"Generating audio with {engine}, voice {voice} "
              f"(normal={normal_rate!r}, slow={slow_rate!r}) ...")
        media_files, failures = add_audio_to_entries(
            entries, Path(args.cache_dir), engine=engine, voice=voice,
            normal_rate=normal_rate, slow_rate=slow_rate,
            edge_pitch=args.edge_pitch, rhvoice_pitch=args.rhvoice_pitch,
            mp3_bitrate=args.mp3_bitrate,
            edge_retries=args.edge_retries, edge_retry_delay=args.edge_retry_delay,
            use_cache=not args.no_cache,
        )

        n_word = sum(1 for e in entries if e.get("audio_tag"))
        n_sentence = sum(1 for e in entries if e.get("example_audio_tag"))
        n_slow = sum(1 for e in entries if e.get("example_audio_slow_tag"))
        total = len(entries)
        print()
        print("Audio generation")
        print("-----------------")
        print(f"Word audio:           {n_word}/{total}")
        print(f"Example audio:        {n_sentence}/{total}")
        print(f"Slow example audio:   {n_slow}/{total}")
        if failures:
            print()
            print("Failed audio:")
            for rank, word, clip_type in failures[:30]:
                print(f"  #{rank} {word} ({clip_type})")
            if len(failures) > 30:
                print(f"  ... and {len(failures) - 30} more (see the failed-audio log)")
            failed_log_path = Path(args.failed_log) if args.failed_log else (
                output_path.with_name(output_path.stem + ".failed.txt")
            )
            write_failed_log(failed_log_path, failures)
            print(f"Full list written to {failed_log_path}")

    deck = build_deck(entries, args.deck_name)
    genanki.Package(deck, media_files=media_files).write_to_file(str(output_path))

    print(f"\nWrote {args.output}")
    if args.csv:
        write_csv(entries, args.csv)
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
