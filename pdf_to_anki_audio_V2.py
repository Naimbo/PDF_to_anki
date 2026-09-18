#!/usr/bin/env python3
"""
pdf_to_anki_audio.py

Same as pdf_to_anki.py, but also generates a spoken-audio pronunciation for
every Russian word and embeds it in the deck, so each card includes a
playable [sound:...] clip alongside the text.

Audio is synthesized OFFLINE (no cloud API, no API key, no internet needed).
Two engines are supported:
  - RHVoice (default when available): a Russian-specific voice used in
    Russian screen readers. Noticeably more natural than robotic formant
    synthesis - clear intonation, correct stress. Linux only.
  - espeak-ng: robotic but installs everywhere (Linux/Mac/Windows) and needs
    no extra voice data. Used automatically if RHVoice isn't installed, or
    force it with --tts-engine espeak.
Either way, output is compressed to mp3 with ffmpeg to keep the deck small.

Requirements:
    pip install pdfplumber genanki --break-system-packages
    System packages (not pip):
        Ubuntu/Debian:  sudo apt-get install rhvoice rhvoice-russian ffmpeg
                        (or, without RHVoice:  sudo apt-get install espeak-ng ffmpeg)
        macOS (brew):   brew install espeak-ng ffmpeg   # RHVoice has no official mac build
        Windows:        install eSpeak NG and ffmpeg, and make sure both
                         are on your PATH   # RHVoice has no official Windows build

Usage:
    python3 pdf_to_anki_audio.py input.pdf -o russian_2000.apkg
    python3 pdf_to_anki_audio.py input.pdf -o russian_2000.apkg --csv out.csv
    python3 pdf_to_anki_audio.py input.pdf -o out.apkg --no-audio           # text only, like the plain script
    python3 pdf_to_anki_audio.py input.pdf -o out.apkg --tts-engine rhvoice --voice Aleksandr
    python3 pdf_to_anki_audio.py input.pdf -o out.apkg --tts-engine espeak --tts-speed 120
"""

import argparse
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Install with:\n  pip install pdfplumber genanki --break-system-packages")

try:
    import genanki
except ImportError:
    sys.exit("Missing dependency. Install with:\n  pip install pdfplumber genanki --break-system-packages")


HEADER_RE = re.compile(
    r'^\s*(?P<rank>\d+)\s*[-–—]\s*(?P<word>.+?)\s*\[(?P<pron>.*?)\]\s*[-–—]\s*(?P<translation>.+?)\s*$'
)
CYRILLIC_RE = re.compile(r'[\u0400-\u04FF]')


# --- PDF parsing (identical to pdf_to_anki.py) ------------------------------

def extract_text(pdf_path: str) -> str:
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n\n".join(chunks)


def parse_entries(raw_text: str) -> list[dict]:
    entries = []
    current = None
    last_field = None

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            last_field = None
            continue

        m = HEADER_RE.match(line)
        if m:
            if current:
                entries.append(current)
            current = {
                "rank": m.group("rank"),
                "word": m.group("word").strip(),
                "pronunciation": m.group("pron").strip(),
                "translation": m.group("translation").strip(),
                "example_ru": "",
                "example_en": "",
            }
            last_field = None
            continue

        if current is None:
            continue

        is_ru = bool(CYRILLIC_RE.search(line))
        field = "example_ru" if is_ru else "example_en"

        if last_field == field and current[field]:
            current[field] = current[field] + " " + line
        else:
            current[field] = (current[field] + " " + line).strip() if current[field] else line
        last_field = field

    if current:
        entries.append(current)

    return entries


def normalize_word(word: str, example_ru: str) -> str:
    lower = word.lower()
    if lower == word:
        return word
    if example_ru and re.search(r'\b' + re.escape(lower) + r'\b', example_ru):
        return lower
    return word


def bold_word_in_sentence(sentence: str, word: str) -> str:
    if not sentence or not word:
        return sentence
    pattern = re.compile(r'\b(' + re.escape(word) + r')\b', flags=re.IGNORECASE | re.UNICODE)
    return pattern.sub(r'<b>\1</b>', sentence, count=1)


def strip_tags(text: str) -> str:
    """Remove HTML tags (e.g. the <b> added around the target word) before feeding text to TTS."""
    return re.sub(r'<[^>]+>', '', text)


def clean_entries(raw_entries: list[dict]) -> list[dict]:
    cleaned = []
    for e in raw_entries:
        word = normalize_word(e["word"], e["example_ru"])
        cleaned.append({
            "rank": e["rank"],
            "word": word,
            "pronunciation": e["pronunciation"],
            "translation": e["translation"],
            "example_ru": bold_word_in_sentence(e["example_ru"], word),
            "example_en": e["example_en"],
        })
    return cleaned


# --- Audio generation --------------------------------------------------------

# --- Audio generation --------------------------------------------------------

RHVOICE_VOICES = [
    "Aleksandr-hq", "Aleksandr", "Anna", "Arina", "Artemiy", "Elena",
    "Evgeniy-Rus", "Irina", "Mikhail", "Pavel", "Tatiana", "Victoria",
    "Vitaliy", "Yuriy",
]


def pick_tts_engine(requested: str) -> str:
    """
    Returns "rhvoice" or "espeak". RHVoice is a purpose-built Russian voice
    and sounds considerably more natural than espeak-ng's formant synthesis,
    so it's preferred when available. Falls back to espeak-ng (installed
    almost everywhere) if RHVoice isn't present, or does what was explicitly
    requested via --tts-engine.
    """
    have_rhvoice = shutil.which("RHVoice-test") is not None
    have_espeak = shutil.which("espeak-ng") is not None

    if requested != "auto":
        if requested == "rhvoice" and not have_rhvoice:
            sys.exit(
                "RHVoice-test not found. Install it with:\n"
                "  sudo apt-get install rhvoice rhvoice-russian\n"
                "(macOS/Windows don't have official RHVoice packages - use --tts-engine espeak instead.)"
            )
        if requested == "espeak" and not have_espeak:
            sys.exit("espeak-ng not found. Install it with:\n  sudo apt-get install espeak-ng")
        return requested

    if have_rhvoice:
        return "rhvoice"
    if have_espeak:
        return "espeak"
    sys.exit(
        "No TTS engine found. Install one of:\n"
        "  sudo apt-get install rhvoice rhvoice-russian   # more natural (Linux)\n"
        "  sudo apt-get install espeak-ng                 # always available, more robotic\n"
        "Or re-run with --no-audio to skip pronunciation audio."
    )


def check_audio_dependencies(engine: str) -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "Missing system tool required for --audio: ffmpeg\n"
            "  Ubuntu/Debian:  sudo apt-get install ffmpeg\n"
            "  macOS:          brew install ffmpeg\n"
            "Or re-run with --no-audio to skip pronunciation audio."
        )


def _wav_to_mp3(wav_path: Path, out_mp3_path: Path, bitrate: str = "96k") -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(out_mp3_path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and out_mp3_path.exists()


def synthesize_text_audio_rhvoice(text: str, out_mp3_path: Path, voice: str,
                                   rate: int = 100, pitch: int = 100, mp3_bitrate: str = "96k") -> bool:
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as tmp_txt:
        tmp_txt.write(text)
        txt_path = Path(tmp_txt.name)
    wav_path = txt_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            # -R 24000 explicitly requests RHVoice's full-quality voice data
            # (some voices default to 16kHz "fast" mode otherwise); -r/-t let
            # you tune pacing/pitch without touching the underlying voice data.
            ["RHVoice-test", "-p", voice, "-R", "24000", "-r", str(rate), "-t", str(pitch),
             "-i", str(txt_path), "-o", str(wav_path)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            return False
        return _wav_to_mp3(wav_path, out_mp3_path, bitrate=mp3_bitrate)
    except subprocess.TimeoutExpired:
        return False
    finally:
        txt_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


def synthesize_text_audio_espeak(text: str, out_mp3_path: Path, voice: str, speed: int, mp3_bitrate: str = "96k") -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = Path(tmp_wav.name)
    try:
        result = subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(wav_path), text],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            return False
        return _wav_to_mp3(wav_path, out_mp3_path, bitrate=mp3_bitrate)
    finally:
        wav_path.unlink(missing_ok=True)


def synthesize_text_audio(text: str, out_mp3_path: Path, engine: str, voice: str, speed: int,
                           rhvoice_rate: int, rhvoice_pitch: int, mp3_bitrate: str) -> bool:
    if engine == "rhvoice":
        return synthesize_text_audio_rhvoice(text, out_mp3_path, voice=voice,
                                              rate=rhvoice_rate, pitch=rhvoice_pitch, mp3_bitrate=mp3_bitrate)
    return synthesize_text_audio_espeak(text, out_mp3_path, voice=voice, speed=speed, mp3_bitrate=mp3_bitrate)


def add_audio_to_entries(entries: list[dict], media_dir: Path, engine: str, voice: str, speed: int,
                          rhvoice_rate: int = 100, rhvoice_pitch: int = 100, mp3_bitrate: str = "96k") -> list[str]:
    """
    Generates TWO mp3s per entry into media_dir: one for the word itself
    (entry["audio_tag"]) and, when an example sentence exists, one for the
    full Russian example sentence (entry["example_audio_tag"]). Returns the
    list of media file paths to bundle into the .apkg. Entries where a given
    clip fails to synthesize just get an empty tag for that clip, so the
    deck still builds.
    """
    media_dir.mkdir(parents=True, exist_ok=True)
    media_files = []
    failed_word = []
    failed_sentence = []

    for e in entries:
        # 1) word pronunciation
        word_filename = f"ru2000_{e['rank']}_word.mp3"
        word_path = media_dir / word_filename
        if synthesize_text_audio(e["word"], word_path, engine, voice, speed, rhvoice_rate, rhvoice_pitch, mp3_bitrate):
            e["audio_tag"] = f"[sound:{word_filename}]"
            media_files.append(str(word_path))
        else:
            e["audio_tag"] = ""
            failed_word.append(e["rank"])

        # 2) full example sentence (Russian), stripped of the <b> markup
        plain_sentence = strip_tags(e["example_ru"]).strip()
        if plain_sentence:
            sentence_filename = f"ru2000_{e['rank']}_sentence.mp3"
            sentence_path = media_dir / sentence_filename
            if synthesize_text_audio(plain_sentence, sentence_path, engine, voice, speed,
                                      rhvoice_rate, rhvoice_pitch, mp3_bitrate):
                e["example_audio_tag"] = f"[sound:{sentence_filename}]"
                media_files.append(str(sentence_path))
            else:
                e["example_audio_tag"] = ""
                failed_sentence.append(e["rank"])
        else:
            e["example_audio_tag"] = ""

    if failed_word:
        print(f"Warning: word audio generation failed for {len(failed_word)} entries "
              f"(ranks: {', '.join(failed_word[:10])}{'...' if len(failed_word) > 10 else ''})")
    if failed_sentence:
        print(f"Warning: sentence audio generation failed for {len(failed_sentence)} entries "
              f"(ranks: {', '.join(failed_sentence[:10])}{'...' if len(failed_sentence) > 10 else ''})")

    return media_files


# --- Anki deck construction -------------------------------------------------

MODEL_ID = 1607392321  # bumped again: new ExampleAudio field
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
.example { font-size: 15px; margin-top: 14px; text-align: left; display: inline-block; }
.example .en { color: #555; }
.rank { font-size: 12px; color: #999; margin-top: 10px; }
.audio { margin-top: 8px; }
"""

RU_EN_QFMT = """
<div>{{Word}}</div>
<div class="audio">{{Audio}}</div>
"""
RU_EN_AFMT = """
{{FrontSide}}
<hr id="answer">
<div class="pron">[{{Pronunciation}}]</div>
<div>{{Translation}}</div>
{{#ExampleRU}}
<div class="example">{{ExampleRU}} {{ExampleAudio}}<br><span class="en">{{ExampleEN}}</span></div>
{{/ExampleRU}}
<div class="rank">#{{Rank}} most frequent</div>
"""

EN_RU_QFMT = """
<div>{{Translation}}</div>
"""
EN_RU_AFMT = """
{{FrontSide}}
<hr id="answer">
<div>{{Word}}</div>
<div class="pron">[{{Pronunciation}}]</div>
<div class="audio">{{Audio}}</div>
{{#ExampleRU}}
<div class="example">{{ExampleRU}} {{ExampleAudio}}<br><span class="en">{{ExampleEN}}</span></div>
{{/ExampleRU}}
<div class="rank">#{{Rank}} most frequent</div>
"""


def build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "Russian 2000 Frequency (RU<->EN, audio)",
        fields=[
            {"name": "Word"},
            {"name": "Pronunciation"},
            {"name": "Translation"},
            {"name": "ExampleRU"},
            {"name": "ExampleEN"},
            {"name": "Rank"},
            {"name": "Audio"},
            {"name": "ExampleAudio"},
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
    for e in entries:
        note = genanki.Note(
            model=model,
            fields=[
                e["word"],
                e["pronunciation"],
                e["translation"],
                e["example_ru"],
                e["example_en"],
                e["rank"],
                e.get("audio_tag", ""),
                e.get("example_audio_tag", ""),
            ],
        )
        deck.add_note(note)
    return deck


def write_csv(entries: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "word", "pronunciation", "translation", "example_ru", "example_en",
                           "audio_tag", "example_audio_tag"]
        )
        writer.writeheader()
        for e in entries:
            writer.writerow(e)


def main():
    parser = argparse.ArgumentParser(description="Convert a Russian word-frequency PDF into an Anki deck, with audio.")
    parser.add_argument("pdf", help="Path to the input PDF")
    parser.add_argument("-o", "--output", default="russian_deck.apkg", help="Output .apkg path")
    parser.add_argument("--deck-name", default="Russian 2000 Most Frequent Words", help="Anki deck name")
    parser.add_argument("--csv", default=None, help="Optional path to also write a CSV backup")
    parser.add_argument("--no-audio", action="store_true", help="Skip pronunciation audio generation")
    parser.add_argument("--tts-engine", choices=["auto", "rhvoice", "espeak"], default="auto",
                         help="TTS engine: rhvoice (natural, Linux-only), espeak (robotic, cross-platform), "
                              "or auto (prefer rhvoice, fall back to espeak) (default: auto)")
    parser.add_argument("--voice", default=None,
                         help="Voice name. RHVoice options: " + ", ".join(RHVOICE_VOICES) +
                              " (default: Anna). For espeak, an espeak-ng voice code (default: ru)")
    parser.add_argument("--tts-speed", type=int, default=140, help="espeak-ng speech rate in words/min (default: 140)")
    parser.add_argument("--rhvoice-rate", type=int, default=100,
                         help="RHVoice speech rate as %% of normal (default: 100). Try 90 for slightly slower/clearer.")
    parser.add_argument("--rhvoice-pitch", type=int, default=100,
                         help="RHVoice pitch as %% of normal (default: 100). Usually best left alone.")
    parser.add_argument("--mp3-bitrate", default="96k",
                         help="mp3 encoding bitrate, e.g. 48k/96k/128k (default: 96k - noticeably cleaner than 48k "
                              "for a modest size increase)")
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        sys.exit(f"File not found: {args.pdf}")

    engine = None
    if not args.no_audio:
        engine = pick_tts_engine(args.tts_engine)
        check_audio_dependencies(engine)
        print(f"Using TTS engine: {engine}")

    print(f"Reading {args.pdf} ...")
    raw_text = extract_text(args.pdf)

    print("Parsing entries ...")
    raw_entries = parse_entries(raw_text)
    entries = clean_entries(raw_entries)
    print(f"Found {len(entries)} entries.")

    missing_examples = [e["rank"] for e in entries if not e["example_ru"] or not e["example_en"]]
    if missing_examples:
        print(f"Warning: {len(missing_examples)} entries are missing an example sentence "
              f"(ranks: {', '.join(missing_examples[:10])}{'...' if len(missing_examples) > 10 else ''})")

    media_files = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        if not args.no_audio:
            print(f"Generating word + example-sentence audio for {len(entries)} entries ...")
            default_voice = "Anna" if engine == "rhvoice" else "ru"
            voice = args.voice or default_voice
            media_files = add_audio_to_entries(
                entries, Path(tmp_dir), engine=engine, voice=voice, speed=args.tts_speed,
                rhvoice_rate=args.rhvoice_rate, rhvoice_pitch=args.rhvoice_pitch, mp3_bitrate=args.mp3_bitrate,
            )
            print(f"Generated {len(media_files)} audio clips.")
        else:
            for e in entries:
                e["audio_tag"] = ""
                e["example_audio_tag"] = ""

        deck = build_deck(entries, args.deck_name)
        genanki.Package(deck, media_files=media_files).write_to_file(args.output)

    print(f"Wrote {args.output}")

    if args.csv:
        write_csv(entries, args.csv)
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
