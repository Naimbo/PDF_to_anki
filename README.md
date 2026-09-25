# Russian Frequency List → Anki Deck (V10)

Converts a PDF list of frequent Russian words (word + pronunciation +
translation + example sentence) into an importable Anki deck, with two-way
cards and spoken audio for both the word and the example sentence (at
normal and slow speed).

## What's in this folder

| File | What it does |
|---|---|
| `pdf_to_anki_edge_V10.py` | The converter: PDF → `.apkg`, with Edge TTS audio, caching, dry-run validation, rank-based range selection, and a parser robust to several real PDF layout quirks (see below). |

Earlier versions (V4–V9) are superseded by this one. V8/V9/V10 specifically
fixed real parsing bugs found by testing against the actual PDF — see
"Parsing edge cases" below for what's now handled correctly.

## What the PDF needs to look like

```
1 - И [i] - And

Вчера она купила фрукты и овощи.
She bought some fruit and vegetables yesterday.
```

`rank - word [pronunciation] - translation`, followed by a Russian example
sentence and its English translation. The parser tolerates several layout
variations within that basic shape — see below.

## Parsing edge cases handled

Real PDFs don't always extract as cleanly as the ideal case above. V10's
parser (`parse_entries()`) specifically handles three patterns found in the
actual source PDF, verified against 86 real entries:

1. **Normal case** — header line, then a separate Russian line, then a
   separate English line.
2. **Russian + English merged onto one extracted line** — some pages lay
   the two out as side-by-side columns, and pdfplumber merges them into a
   single line of text (e.g. `Я могу зайти завтра...    I can drop by...`).
   The parser finds a content-based split point (which word the language
   switches at) rather than assuming a fixed position — so it still works
   regardless of exact spacing, and won't be fooled by a stray foreign word
   or name embedded naturally in either sentence.
3. **A translation that wraps across two or more PDF lines** — sometimes
   inside an unclosed parenthesis (`"...(in combination with 'такой',
   'тот',"` then `"'то')"` on the next line), sometimes as a long
   definition with no parentheses at all (`"A possessive pronoun common for
   all"` then `"personal pronouns (...) and refers"` then `"to the subject
   of the sentence..."`). The parser recognizes both: an unclosed `(`
   signals the header isn't finished, and — separately — any English-heavy
   line appearing *before* a Russian example has been captured yet for that
   entry can only be more definition text, never a genuine example
   (this document's format is always Russian-example-then-English-example,
   never the reverse). A translation with several *complete* parenthetical
   groups on one line, like `"(one) May, (one) can, (it is) possible"`, is
   correctly left alone since its parenthesis count is already balanced.

Every one of these was found and fixed against real PDF content, not
hypothetical cases — worth knowing if you ever see a new kind of mangled
card: it's likely a fourth pattern not covered above, and the fix should
follow the same principle (look at actual structure/content, never a
hardcoded page/rank special case).

## What the cards look like

Both card types share the identical example layout:

- **Russian → English**: front shows the word + its audio. Back reveals
  `Russian Pronunciation: [...]`, the translation, then the example sentence
  with 🐢 (slow) and 🐇 (normal) audio buttons, the English translation, and
  the frequency rank.
- **English → Russian**: front shows the translation. Back reveals the word,
  pronunciation, its audio, then the *same* example block (🐢/🐇 audio) as
  the other card.

## Setup (Linux Mint / Ubuntu-based)

**1. Python packages:**
```bash
python3 -m venv anki_env
source anki_env/bin/activate
pip install pdfplumber genanki edge-tts
```

**2. (Optional) offline engines**, only if you want RHVoice or eSpeak instead
of the default Edge TTS:
```bash
sudo apt install rhvoice rhvoice-russian espeak-ng ffmpeg
```
Edge TTS itself needs no system packages — just the `edge-tts` Python
package and an internet connection.

## Usage

**1. Check the parsing before running any TTS — no audio, no files:**
```bash
python3 pdf_to_anki_edge_V10.py words.pdf --start 501 --end 1000 --dry-run
```
Check `Missing examples`, `Mixed-language examples`, `Rank mismatches`, and
`Unbolded target words` are all low/zero.

**2. If something still looks wrong, inspect exactly what the parser saw:**
```bash
python3 pdf_to_anki_edge_V10.py words.pdf --debug-parse --dry-run --no-audio | less
```
Prints every raw extracted line, what pattern it matched (bilingual split /
translation continuation / single-language), and the resulting field —
pipe through `grep`/`less`/`tail` to focus on a specific page or rank.

**3. Generate the deck:**
```bash
python3 pdf_to_anki_edge_V10.py words.pdf --start 501 --end 1000 -o russian_501_1000.apkg
```

**4. Import into Anki:** File → Import... → select the `.apkg`.

## Command lines for chunked decks

```bash
python3 pdf_to_anki_edge_V10.py words.pdf --start 1    --end 500  --speed 100 --slow-speed 75 -o russian_1_500.apkg
python3 pdf_to_anki_edge_V10.py words.pdf --start 501  --end 1000 --speed 100 --slow-speed 75 -o russian_501_1000.apkg
python3 pdf_to_anki_edge_V10.py words.pdf --start 1001 --end 1500 --speed 100 --slow-speed 75 -o russian_1001_1500.apkg
python3 pdf_to_anki_edge_V10.py words.pdf --start 1501 --end 2000 --speed 100 --slow-speed 75 -o russian_1501_2000.apkg
```
Ranges are based on the PDF's own printed rank number, so these four runs
partition the word list without gaps or overlaps. Running them back-to-back
benefits from the audio cache — nothing is resynthesized unless the text,
voice, engine, rate, or pitch actually differs.

## All options

| Flag | Default | What it does |
|---|---|---|
| `pdf` (positional) | — | Input PDF. Omit if using `--preview`. |
| `--preview` | off | Build the built-in 5-word test set instead of reading a PDF. |
| `--limit X` | — | Use only the first X parsed words (by position). |
| `--start N` | — | Only words with **printed rank** ≥ N. |
| `--end N` | — | Only words with **printed rank** ≤ N. |
| `--dry-run` | off | Validate and report; generate no audio, write no files. |
| `--debug-parse` | off | Print every raw line and how it was classified during parsing. |
| `-o, --output` | `russian_deck.apkg` | Output path. |
| `--deck-name` | `Russian Most Frequent Words` | Name shown in Anki. |
| `--csv` | — | Also write a CSV backup. |
| `--no-audio` | off | Skip audio entirely (text-only cards). |
| `--tts-engine` | `edge` | `edge`, `rhvoice`, `espeak`, or `auto`. |
| `--voice` | engine-specific | Edge: `ru-RU-DmitryNeural`. RHVoice: `Anna`. eSpeak: `ru`. |
| `--speed` | — | Convenience: normal speed, % of default, same units on every engine. |
| `--slow-speed` | — | Convenience: speed for the slow sentence clip, % of default. |
| `--edge-rate` | `+0%` | Edge-specific normal rate (legacy; `--speed` overrides it if given). |
| `--edge-pitch` | `+0Hz` | Edge pitch. |
| `--edge-retries` | `3` | Retry attempts for Edge's intermittent "No audio was received" error. |
| `--edge-retry-delay` | `1.5` | Seconds between retries. |
| `--slow-rate` | `-50%` | Edge-specific slow rate (legacy). |
| `--tts-speed` | `140` | eSpeak words/minute (legacy). |
| `--rhvoice-rate` | `100` | RHVoice %-of-normal (legacy). |
| `--rhvoice-pitch` | `100` | RHVoice pitch. |
| `--mp3-bitrate` | `96k` | mp3 quality for RHVoice/eSpeak output. |
| `--cache-dir` | `tts_cache` | Where generated audio is cached and reused. |
| `--no-cache` | off | Don't reuse cached audio (still writes new clips into the cache). |
| `--failed-log` | `<output>.failed.txt` | Where the list of failed clips gets written, if any. |

**Mutually exclusive:** `--limit` vs. `--start`/`--end`; `--preview` vs. a PDF path.

## Rank vs. position

`--start`/`--end` filter by the PDF's **printed rank**, not internal parse
order, so adjacent chunk decks (1–500, 501–1000, ...) partition the real
numbering correctly even if an entry somewhere failed to parse (that shows
up as a "rank mismatch" warning instead of silently shifting everything
after it). Requesting a range beyond what's available clamps and reports
rather than crashing:
```
Requested range: 1900-2500
Available entries: 1-2000
Selected entries: 1900-2000 (101 entries, 202 cards)
```

## Audio caching

Every clip is cached under `--cache-dir` (default `./tts_cache`), keyed by a
hash of **engine + voice + rate + pitch + text** — a clip is only reused
when all five match exactly. Reruns after a crash, or overlapping
`--start`/`--end` ranges, don't resynthesize what already succeeded.

## TTS failure handling

A failed clip never fails the whole run. At the end:
```
Audio generation
-----------------
Word audio:           97/100
Example audio:        95/100
Slow example audio:   94/100

Failed audio:
  #54 рука (word)
Full list written to russian_deck.failed.txt
```
Edge's "No audio was received" error is a known issue in the edge-tts
library itself during sustained use — the script retries automatically
(`--edge-retries`, default 3) before giving up on a clip.

## Known data-quality checks (all visible via `--dry-run`)

- **Rank mismatches** — printed rank disagrees with actual position.
- **Missing examples** — an entry has no Russian or English example.
- **Mixed-language examples** — `example_ru` still looks English-heavy after
  parsing (leftover dictionary/footnote text).
- **Unbolded target words** — the vocabulary word wasn't found verbatim in
  its own example sentence. Usually one of two causes, both harmless and
  left as-is rather than "fixed" automatically:
  - an **inflected form** in the example (e.g. `рука` vs. `руку`, `мочь`
    vs. `могу`) — no morphological matching is attempted, by design;
  - a **font-encoding quirk in the source PDF** where a header word was set
    in a font that maps visually-identical Cyrillic letters to Latin code
    points (e.g. `co` typed as Latin `c`+`o` rather than Cyrillic `с`+`о`,
    even though it displays identically) — the word and example are
    genuinely different Unicode text even though they look the same on
    screen. Nothing to fix in the parser; just worth knowing the card's
    word still sounds/displays correctly, it just won't get bolded in the
    example.

## Troubleshooting

- **`edge-tts is not installed`** → `pip install edge-tts` inside your venv.
- **A card's translation and example text look mixed together** → run
  `--debug-parse` on that page/rank range and check which pattern it
  matched; if it doesn't match any of the three cases described above,
  that's a genuinely new layout variant — share the raw PDF text (or the
  PDF page itself) for that entry so the parser can be extended the same
  principled way the existing three were.
- **A lot of entries in "Failed audio"** → almost always transient Edge
  connectivity; rerun the same command — the cache means anything that
  already succeeded won't be redone.
