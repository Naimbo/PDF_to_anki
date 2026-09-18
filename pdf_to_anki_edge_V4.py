#!/usr/bin/env python3
"""Convert a Russian frequency-list PDF into an Anki deck with TTS audio.

The expected PDF layout is:

    1 - И [i] - And
    Вчера она купила фрукты и овощи.
    She bought some fruit and vegetables yesterday.

By default, audio is generated online with Microsoft Edge TTS using the
Russian male voice ``ru-RU-DmitryNeural``.  Each note produces two Anki cards:
Russian -> English and English -> Russian.

Install:
    python3 -m pip install pdfplumber genanki edge-tts

Build the five-word preview deck (no PDF required):
    python3 pdf_to_anki_edge.py --preview -o russian_preview_5_words.apkg

Build the complete deck:
    python3 pdf_to_anki_edge.py words.pdf -o russian_frequency.apkg

Build a deck containing only the first 100 words (200 cards):
    python3 pdf_to_anki_edge.py words.pdf --limit 100 -o russian_first_100.apkg

Edge TTS needs an internet connection and sends the Russian text to
Microsoft's speech service.  RHVoice and eSpeak remain available as optional
offline backends, but require their system packages and ffmpeg.
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


# --- PDF parsing ------------------------------------------------------------

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
            last_field = None
            continue

        if current is None:
            continue

        field = "example_ru" if CYRILLIC_RE.search(line) else "example_en"
        if last_field == field and current[field]:
            current[field] += " " + line
        else:
            current[field] = (
                (current[field] + " " + line).strip()
                if current[field]
                else line
            )
        last_field = field

    if current:
        entries.append(current)
    return entries


def normalize_word(word: str) -> str:
    """Use dictionary-style lowercase for the vocabulary item."""
    return word.strip().lower()


def bold_word_in_sentence(sentence: str, word: str) -> str:
    """Bold only the first exact occurrence of the card's target word."""
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


# --- Audio generation -------------------------------------------------------

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
    sys.exit(
        "No TTS engine is available. Install edge-tts, RHVoice, or eSpeak NG."
    )


def check_audio_dependencies(engine: str) -> None:
    if engine != "edge" and shutil.which("ffmpeg") is None:
        sys.exit(
            "ffmpeg is required for RHVoice/eSpeak MP3 conversion.\n"
            "On Linux Mint/Ubuntu:\n"
            "  sudo apt install ffmpeg"
        )


def _wav_to_mp3(
    wav_path: Path, out_mp3_path: Path, bitrate: str = "96k"
) -> bool:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            str(out_mp3_path),
        ],
        capture_output=True,
        text=True,
    )
    return (
        result.returncode == 0
        and out_mp3_path.exists()
        and out_mp3_path.stat().st_size > 0
    )


def synthesize_text_audio_edge(
    text: str,
    out_mp3_path: Path,
    voice: str,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bool:
    try:
        communicator = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        communicator.save_sync(str(out_mp3_path))
        return out_mp3_path.exists() and out_mp3_path.stat().st_size > 0
    except Exception as exc:
        print(f"Edge TTS error for {text!r}: {exc}", file=sys.stderr)
        out_mp3_path.unlink(missing_ok=True)
        return False


def synthesize_text_audio_rhvoice(
    text: str,
    out_mp3_path: Path,
    voice: str,
    rate: int = 100,
    pitch: int = 100,
    mp3_bitrate: str = "96k",
) -> bool:
    with tempfile.NamedTemporaryFile(
        suffix=".txt", mode="w", delete=False, encoding="utf-8"
    ) as tmp_txt:
        tmp_txt.write(text)
        txt_path = Path(tmp_txt.name)
    wav_path = txt_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            [
                "RHVoice-test",
                "-p",
                voice,
                "-R",
                "24000",
                "-r",
                str(rate),
                "-t",
                str(pitch),
                "-i",
                str(txt_path),
                "-o",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if (
            result.returncode != 0
            or not wav_path.exists()
            or wav_path.stat().st_size == 0
        ):
            return False
        return _wav_to_mp3(wav_path, out_mp3_path, mp3_bitrate)
    except subprocess.TimeoutExpired:
        return False
    finally:
        txt_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


def synthesize_text_audio_espeak(
    text: str,
    out_mp3_path: Path,
    voice: str,
    speed: int,
    mp3_bitrate: str = "96k",
) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = Path(tmp_wav.name)
    try:
        result = subprocess.run(
            [
                "espeak-ng",
                "-v",
                voice,
                "-s",
                str(speed),
                "-w",
                str(wav_path),
                text,
            ],
            capture_output=True,
            text=True,
        )
        if (
            result.returncode != 0
            or not wav_path.exists()
            or wav_path.stat().st_size == 0
        ):
            return False
        return _wav_to_mp3(wav_path, out_mp3_path, mp3_bitrate)
    finally:
        wav_path.unlink(missing_ok=True)


def synthesize_text_audio(
    text: str,
    out_mp3_path: Path,
    engine: str,
    voice: str,
    speed: int,
    rhvoice_rate: int,
    rhvoice_pitch: int,
    mp3_bitrate: str,
    edge_rate: str,
    edge_pitch: str,
) -> bool:
    if engine == "edge":
        return synthesize_text_audio_edge(
            text,
            out_mp3_path,
            voice=voice,
            rate=edge_rate,
            pitch=edge_pitch,
        )
    if engine == "rhvoice":
        return synthesize_text_audio_rhvoice(
            text,
            out_mp3_path,
            voice=voice,
            rate=rhvoice_rate,
            pitch=rhvoice_pitch,
            mp3_bitrate=mp3_bitrate,
        )
    return synthesize_text_audio_espeak(
        text,
        out_mp3_path,
        voice=voice,
        speed=speed,
        mp3_bitrate=mp3_bitrate,
    )


def add_audio_to_entries(
    entries: list[dict],
    media_dir: Path,
    engine: str,
    voice: str,
    speed: int,
    rhvoice_rate: int,
    rhvoice_pitch: int,
    mp3_bitrate: str,
    edge_rate: str,
    edge_pitch: str,
) -> list[str]:
    media_dir.mkdir(parents=True, exist_ok=True)
    media_files = []
    failed = []

    for index, entry in enumerate(entries, start=1):
        print(f"  Audio {index}/{len(entries)}: {entry['word']}")
        clips = [
            ("word", entry["word"], "audio_tag"),
            (
                "sentence",
                strip_tags(entry["example_ru"]).strip(),
                "example_audio_tag",
            ),
        ]
        for clip_name, text, tag_field in clips:
            if not text:
                entry[tag_field] = ""
                continue
            filename = f"ru_frequency_{entry['rank']}_{clip_name}.mp3"
            path = media_dir / filename
            ok = synthesize_text_audio(
                text,
                path,
                engine=engine,
                voice=voice,
                speed=speed,
                rhvoice_rate=rhvoice_rate,
                rhvoice_pitch=rhvoice_pitch,
                mp3_bitrate=mp3_bitrate,
                edge_rate=edge_rate,
                edge_pitch=edge_pitch,
            )
            if ok:
                entry[tag_field] = f"[sound:{filename}]"
                media_files.append(str(path))
            else:
                entry[tag_field] = ""
                failed.append(f"#{entry['rank']} {clip_name}")

    if failed:
        print("Warning: audio failed for " + ", ".join(failed), file=sys.stderr)
    return media_files


# --- Anki deck construction -------------------------------------------------

MODEL_ID = 1607392407
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
"""

RU_EN_QFMT = """
<div>{{Word}}</div>
<div class="audio">{{Audio}}</div>
"""

RU_EN_AFMT = """
{{FrontSide}}
<hr id="answer">
<div class="pron">Russian Pronunciation: [{{Pronunciation}}]</div>
<div>{{Translation}}</div>
{{#ExampleRU}}
<div class="example">{{ExampleRU}} {{ExampleAudio}}<br>
<span class="en">{{ExampleEN}}</span></div>
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
<div class="pron">Russian Pronunciation: [{{Pronunciation}}]</div>
<div class="audio">{{Audio}}</div>
{{#ExampleRU}}
<div class="example">{{ExampleRU}} {{ExampleAudio}}<br>
<span class="en">{{ExampleEN}}</span></div>
{{/ExampleRU}}
<div class="rank">#{{Rank}} most frequent</div>
"""


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
        ],
        templates=[
            {
                "name": "Russian -> English",
                "qfmt": RU_EN_QFMT,
                "afmt": RU_EN_AFMT,
            },
            {
                "name": "English -> Russian",
                "qfmt": EN_RU_QFMT,
                "afmt": EN_RU_AFMT,
            },
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
            ],
        )
        deck.add_note(note)
    return deck


def write_csv(entries: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "rank",
                "word",
                "pronunciation",
                "translation",
                "example_ru",
                "example_en",
                "audio_tag",
                "example_audio_tag",
            ],
        )
        writer.writeheader()
        writer.writerows(entries)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a Russian frequency PDF into an Anki audio deck."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        help="Input PDF. Not required when --preview is used.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Build the built-in five-word preview instead of reading a PDF.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="X",
        help=(
            "Use only the first X words. Each word creates two cards: "
            "Russian -> English and English -> Russian."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default="russian_deck.apkg",
        help="Output .apkg path (default: russian_deck.apkg).",
    )
    parser.add_argument(
        "--deck-name",
        default="Russian Most Frequent Words",
        help="Name shown inside Anki.",
    )
    parser.add_argument("--csv", help="Optional CSV backup path.")
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Build cards without generating audio.",
    )
    parser.add_argument(
        "--tts-engine",
        choices=["edge", "auto", "rhvoice", "espeak"],
        default="edge",
        help="TTS backend (default: edge). Edge is online.",
    )
    parser.add_argument(
        "--voice",
        help=(
            "Voice name. Edge default: ru-RU-DmitryNeural; "
            "RHVoice default: Anna; eSpeak default: ru."
        ),
    )
    parser.add_argument(
        "--edge-rate",
        default="+0%",
        help="Edge speaking rate, such as -10%% or +20%% (default: +0%%).",
    )
    parser.add_argument(
        "--edge-pitch",
        default="+0Hz",
        help="Edge pitch, such as -10Hz or +10Hz (default: +0Hz).",
    )
    parser.add_argument(
        "--tts-speed",
        type=int,
        default=140,
        help="eSpeak speed in words/minute (default: 140).",
    )
    parser.add_argument("--rhvoice-rate", type=int, default=100)
    parser.add_argument("--rhvoice-pitch", type=int, default=100)
    parser.add_argument("--mp3-bitrate", default="96k")
    args = parser.parse_args()

    if not args.preview and not args.pdf:
        parser.error("provide a PDF path or use --preview")
    if args.preview and args.pdf:
        parser.error("use either a PDF path or --preview, not both")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
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
        raw_entries = parse_entries(extract_text(args.pdf))

    entries = clean_entries(raw_entries)
    if not entries:
        sys.exit("No entries were found. Check the PDF text layout.")
    total_entries = len(entries)
    if args.limit is not None:
        entries = entries[: args.limit]
        print(
            f"Using {len(entries)} of {total_entries} entries "
            f"({len(entries) * 2} cards)."
        )
    else:
        print(f"Found {total_entries} entries ({total_entries * 2} cards).")

    missing_examples = [
        entry["rank"]
        for entry in entries
        if not entry["example_ru"] or not entry["example_en"]
    ]
    if missing_examples:
        print(
            "Warning: entries missing an example: "
            + ", ".join(missing_examples[:20]),
            file=sys.stderr,
        )

    media_files = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        if args.no_audio:
            for entry in entries:
                entry["audio_tag"] = ""
                entry["example_audio_tag"] = ""
        else:
            engine = pick_tts_engine(args.tts_engine)
            check_audio_dependencies(engine)
            if engine == "edge":
                default_voice = "ru-RU-DmitryNeural"
            elif engine == "rhvoice":
                default_voice = "Anna"
            else:
                default_voice = "ru"
            voice = args.voice or default_voice
            print(f"Generating audio with {engine}, voice {voice} ...")
            media_files = add_audio_to_entries(
                entries,
                Path(tmp_dir),
                engine=engine,
                voice=voice,
                speed=args.tts_speed,
                rhvoice_rate=args.rhvoice_rate,
                rhvoice_pitch=args.rhvoice_pitch,
                mp3_bitrate=args.mp3_bitrate,
                edge_rate=args.edge_rate,
                edge_pitch=args.edge_pitch,
            )
            print(f"Generated {len(media_files)} audio clips.")

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        deck = build_deck(entries, args.deck_name)
        genanki.Package(deck, media_files=media_files).write_to_file(
            str(output_path)
        )

    print(f"Wrote {args.output}")
    if args.csv:
        write_csv(entries, args.csv)
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
