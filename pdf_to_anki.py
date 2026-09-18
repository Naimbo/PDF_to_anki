#!/usr/bin/env python3
"""
pdf_to_anki.py

Converts a PDF list of frequent Russian words (formatted as entries like:

    1 - И [i] - And

    Вчера она купила фрукты и овощи.
    She bought some fruit and vegetables yesterday.

) into an Anki-importable deck (.apkg), with two card types per word:
  - Russian -> English (front: word + pronunciation, back: translation + examples)
  - English -> Russian (front: translation, back: word + pronunciation + examples)

Requirements:
    pip install pdfplumber genanki --break-system-packages

Usage:
    python3 pdf_to_anki.py input.pdf -o russian_2000.apkg --deck-name "Russian 2000"
    python3 pdf_to_anki.py input.pdf -o russian_2000.apkg --csv russian_2000.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Install with:\n  pip install pdfplumber genanki --break-system-packages")

try:
    import genanki
except ImportError:
    sys.exit("Missing dependency. Install with:\n  pip install pdfplumber genanki --break-system-packages")


# Matches a header line like: "123 - слово [pronunciation] - translation, another"
HEADER_RE = re.compile(
    r'^\s*(?P<rank>\d+)\s*[-–—]\s*(?P<word>.+?)\s*\[(?P<pron>.*?)\]\s*[-–—]\s*(?P<translation>.+?)\s*$'
)

CYRILLIC_RE = re.compile(r'[\u0400-\u04FF]')


def extract_text(pdf_path: str) -> str:
    """Pull raw text out of the PDF, page by page."""
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            chunks.append(text)
    return "\n\n".join(chunks)


def parse_entries(raw_text: str) -> list[dict]:
    """
    Line-by-line state machine (blank lines are not reliable in
    PDF-extracted text - many extractors collapse vertical whitespace
    into a single '\\n' rather than a blank line). A line matching
    HEADER_RE starts a new entry. Every line after that, up until the
    next header, is either a wrapped continuation of the Russian example
    sentence or the English example sentence, distinguished by whether it
    contains Cyrillic script. Consecutive same-script lines are joined,
    which handles sentences that wrap across more than one line.
    """
    entries = []
    current = None
    last_field = None  # "ru" or "en" - which field the previous line extended

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
            # Stray text before the first recognized header (e.g. a title page) - skip it.
            continue

        is_ru = bool(CYRILLIC_RE.search(line))
        field = "example_ru" if is_ru else "example_en"

        if last_field == field and current[field]:
            current[field] = current[field] + " " + line
        else:
            # First line for this field on this entry (or switching fields) -
            # only overwrite if empty, otherwise append as a new sentence.
            current[field] = (current[field] + " " + line).strip() if current[field] else line
        last_field = field

    if current:
        entries.append(current)

    return entries


def normalize_word(word: str, example_ru: str) -> str:
    """
    List headers capitalize the first letter of every word (list-style
    formatting), which isn't necessarily how the word is actually cased.
    We lowercase it UNLESS the example sentence gives no evidence the
    lowercase form is used (e.g. a proper noun like "Россия" that only
    ever appears capitalized) - in that case we leave it as-is to avoid
    guessing wrong.
    """
    lower = word.lower()
    if lower == word:
        return word
    if example_ru and re.search(r'\b' + re.escape(lower) + r'\b', example_ru, flags=re.IGNORECASE | re.UNICODE):
        # Only treat as evidence if a lowercase occurrence actually exists
        if re.search(r'\b' + re.escape(lower) + r'\b', example_ru):
            return lower
    return word


def bold_word_in_sentence(sentence: str, word: str) -> str:
    """Wrap the target word in <b> within an example sentence, case-insensitively."""
    if not sentence or not word:
        return sentence
    pattern = re.compile(r'\b(' + re.escape(word) + r')\b', flags=re.IGNORECASE | re.UNICODE)
    return pattern.sub(r'<b>\1</b>', sentence, count=1)


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


# --- Anki deck construction -------------------------------------------------

MODEL_ID = 1607392319  # fixed so re-running the script updates the same note type
DECK_ID = 2059852317   # fixed so re-running the script updates the same deck

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
"""

RU_EN_QFMT = """
<div>{{Word}}</div>
<div class="pron">[{{Pronunciation}}]</div>
"""
RU_EN_AFMT = """
{{FrontSide}}
<hr id="answer">
<div>{{Translation}}</div>
{{#ExampleRU}}
<div class="example">{{ExampleRU}}<br><span class="en">{{ExampleEN}}</span></div>
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
{{#ExampleRU}}
<div class="example">{{ExampleRU}}<br><span class="en">{{ExampleEN}}</span></div>
{{/ExampleRU}}
<div class="rank">#{{Rank}} most frequent</div>
"""

def build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "Russian 2000 Frequency (RU<->EN)",
        fields=[
            {"name": "Word"},
            {"name": "Pronunciation"},
            {"name": "Translation"},
            {"name": "ExampleRU"},
            {"name": "ExampleEN"},
            {"name": "Rank"},
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
            ],
        )
        deck.add_note(note)
    return deck


def write_csv(entries: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["rank", "word", "pronunciation", "translation", "example_ru", "example_en"]
        )
        writer.writeheader()
        for e in entries:
            writer.writerow(e)


def main():
    parser = argparse.ArgumentParser(description="Convert a Russian word-frequency PDF into an Anki deck.")
    parser.add_argument("pdf", help="Path to the input PDF")
    parser.add_argument("-o", "--output", default="russian_deck.apkg", help="Output .apkg path")
    parser.add_argument("--deck-name", default="Russian 2000 Most Frequent Words", help="Anki deck name")
    parser.add_argument("--csv", default=None, help="Optional path to also write a CSV backup")
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        sys.exit(f"File not found: {args.pdf}")

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

    deck = build_deck(entries, args.deck_name)
    genanki.Package(deck).write_to_file(args.output)
    print(f"Wrote {args.output}")

    if args.csv:
        write_csv(entries, args.csv)
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
