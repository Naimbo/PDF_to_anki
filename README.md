# Russian Frequency List → Anki Deck (V7)

Converts a PDF list of frequent Russian words (word + pronunciation +
translation + example sentence) into an importable Anki deck, with two-way
cards and spoken audio for both the word and the example sentence (at
normal and slow speed).

## What's in this folder

| File | What it does |
|---|---|
| `pdf_to_anki_edge_V7.py` | The converter: PDF → `.apkg`, with Edge TTS audio, caching, dry-run validation, and rank-based range selection. |

Earlier versions (V4–V6) are superseded by this one — V7 is a drop-in
replacement with everything they had, plus the fixes and features below.

## What the PDF needs to look like

```
1 - И [i] - And

Вчера она купила фрукты и овощи.
She bought some fruit and vegetables yesterday.
```

`rank - word [pronunciation] - translation`, followed by a Russian example
sentence and its English translation. The parser tolerates sentences
wrapping across multiple lines.

## What the cards look like

Both card types now share the identical example layout:

- **Russian → English**: front shows the word + its audio. Back reveals
  `Russian Pronunciation: [...]`, the translation, then the example sentence
  with 🐢 (slow) and 🐇 (normal) audio buttons, the English translation, and
  the frequency rank.
- **English → Russian**: front shows the translation. Back reveals the word,
  pronunciation, its audio, then the *same* example block (🐢/🐇 audio) as
  the other card. Previously this card was missing the slow audio and used
  different markup — that's fixed; both cards are now generated from one
  shared template piece so they can't drift apart again.

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
package and an internet connection (it sends the Russian text to
Microsoft's speech service to generate audio).

## Usage

**1. Validate first — no audio, no files, just a report:**
```bash
python3 pdf_to_anki_edge_V7.py words.pdf --start 501 --end 1000 --dry-run
```
Check `Missing examples`, `Mixed-language examples`, `Rank mismatches`, and
`Unbolded target words` are all low/zero before committing to a real run.

**2. Generate the deck:**
```bash
python3 pdf_to_anki_edge_V7.py words.pdf --start 501 --end 1000 -o russian_501_1000.apkg
```

**3. Import into Anki:** File → Import... → select the `.apkg`.

## Command lines you asked for

**Test deck of 5 cards, normal speed 100%, slow speed 75%:**
```bash
python3 pdf_to_anki_edge_V7.py --preview --speed 100 --slow-speed 75 -o test_5.apkg
```

**Full set of four chunk decks, same speeds, 0–500 / 501–1000 / 1001–1500 / 1501–2000:**
```bash
python3 pdf_to_anki_edge_V7.py words.pdf --start 1    --end 500  --speed 100 --slow-speed 75 -o russian_1_500.apkg
python3 pdf_to_anki_edge_V7.py words.pdf --start 501  --end 1000 --speed 100 --slow-speed 75 -o russian_501_1000.apkg
python3 pdf_to_anki_edge_V7.py words.pdf --start 1001 --end 1500 --speed 100 --slow-speed 75 -o russian_1001_1500.apkg
python3 pdf_to_anki_edge_V7.py words.pdf --start 1501 --end 2000 --speed 100 --slow-speed 75 -o russian_1501_2000.apkg
```
Ranges are based on the PDF's own printed rank number, so these four runs
partition the word list without gaps or overlaps (see "Rank vs. position"
below for why that matters). Running these back-to-back also benefits from
the audio cache — nothing is resynthesized between runs unless the text,
voice, engine, rate, or pitch actually differs.

**Run `--dry-run` first on each range** to sanity-check before spending time
on the real TTS pass:
```bash
python3 pdf_to_anki_edge_V7.py words.pdf --start 1 --end 500 --dry-run
```

## All options

| Flag | Default | What it does |
|---|---|---|
| `pdf` (positional) | — | Input PDF. Omit if using `--preview`. |
| `--preview` | off | Build the built-in 5-word test set instead of reading a PDF. |
| `--limit X` | — | Use only the first X parsed words (by position). |
| `--start N` | — | Only words with **printed rank** ≥ N. |
| `--end N` | — | Only words with **printed rank** ≤ N. |
| `--dry-run` | off | Validate and report; generate no audio, write no files. |
| `-o, --output` | `russian_deck.apkg` | Output path. |
| `--deck-name` | `Russian Most Frequent Words` | Name shown in Anki. |
| `--csv` | — | Also write a CSV backup. |
| `--no-audio` | off | Skip audio entirely (text-only cards). |
| `--tts-engine` | `edge` | `edge`, `rhvoice`, `espeak`, or `auto`. |
| `--voice` | engine-specific | Edge: `ru-RU-DmitryNeural`. RHVoice: `Anna`. eSpeak: `ru`. |
| `--speed` | — | Convenience: normal speed, % of default, same units on every engine. Overrides the engine-specific flag below for this run. |
| `--slow-speed` | — | Convenience: speed for the slow sentence clip, % of default. |
| `--edge-rate` | `+0%` | Edge-specific normal rate (legacy; use `--speed` instead if you don't need per-engine control). |
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

**`--speed`/`--slow-speed` vs. the legacy per-engine flags:** all the old
flags (`--edge-rate`, `--rhvoice-rate`, `--tts-speed`, `--slow-rate`, etc.)
still work exactly as before if you don't touch the new ones. If you pass
`--speed`/`--slow-speed`, they override the corresponding legacy value *for
that run only*, and the script prints a note saying so. They're independent:
`--speed` alone does not change what the slow clip's default is computed
from — it still falls back to half of whatever `--rhvoice-rate`/`--tts-speed`
already is unless you also pass `--slow-speed` explicitly.

## Rank vs. position — why `--start`/`--end` use the printed rank

Every entry gets both a `seq` (its position among successfully parsed
entries) and a `rank` (the number printed next to it in the PDF). These
usually agree, but if anything upstream fails to parse — a header split
across two lines, an OCR glitch — they can diverge. `--start`/`--end` filter
on the **printed rank**, specifically so that four adjacent chunk decks
(0–500, 501–1000, ...) partition the real numbering with a gap wherever
something's missing, rather than everything after a dropped entry silently
shifting by one and causing an overlap or gap between decks. If a mismatch
is ever detected, you'll see it as a "rank mismatches" warning (and in
`--dry-run` output) rather than it happening invisibly.

Requesting a range that exceeds what's available doesn't crash — it clamps
and tells you what happened:
```
Requested range: 1900-2500
Available entries: 1-2000
Selected entries: 1900-2000 (101 entries, 202 cards)
```

## Audio caching

Every generated clip is cached under `--cache-dir` (default `./tts_cache`),
keyed by a hash of **engine + voice + rate + pitch + text** — so a clip is
only ever reused when all five of those actually match. Change any one of
them and you get a fresh clip, not a wrong one. This means:

- Rerunning after a crash or a batch of failures doesn't resynthesize
  everything from scratch — only what's missing.
- Building the four chunk decks in sequence benefits from any accidental
  overlap in words/sentences between them.
- Use `--no-cache` to force a clean regeneration without deleting the cache
  directory (new clips still get written into it).

## TTS failure handling

A failed clip never fails the whole run — the note just gets an empty audio
field for that clip. At the end you get a breakdown:
```
Audio generation
-----------------
Word audio:           97/100
Example audio:        95/100
Slow example audio:   94/100

Failed audio:
  #54 рука (word)
  ...
Full list written to russian_deck.failed.txt
```
That file persists even after your terminal closes, so you can decide later
whether to investigate or just rerun that range (the cache means you won't
pay to resynthesize what already succeeded).

Edge TTS's "No audio was received" error is a known, long-standing issue in
the edge-tts library itself (see rany2/edge-tts on GitHub) — an intermittent
hiccup talking to Microsoft's servers during sustained use, not something
wrong with your input. The script retries automatically (`--edge-retries`,
default 3) before giving up on a given clip.

## Known data-quality checks (all visible via `--dry-run` and as warnings)

- **Rank mismatches** — printed rank disagrees with actual position (see above).
- **Missing examples** — an entry has no Russian or English example sentence.
- **Mixed-language examples** — `example_ru` still looks English-heavy after
  parsing, usually leftover dictionary/footnote text that slipped past the
  line classifier. Worth checking that entry in the source PDF.
- **Unbolded target words** — the vocabulary word wasn't found verbatim in
  its own example sentence, almost always because the example uses an
  inflected form (e.g. `рука` vs. `руку`). The script does **not** attempt
  any morphological matching to fix this automatically — a naive fix risks
  false positives on short words, and real Russian lemmatization is a much
  bigger undertaking than this script's scope. These entries just get
  flagged so you can see them; the card still works, it just won't have the
  word bolded in that sentence.

## Troubleshooting

- **`edge-tts is not installed`** → `pip install edge-tts` inside your venv.
- **`No TTS engine is available`** (when using `--tts-engine auto`) → install
  at least one of edge-tts, RHVoice, or eSpeak.
- **A lot of entries in "Failed audio"** → almost always transient Edge
  connectivity; rerun the same command — the cache means everything that
  already succeeded won't be redone, so a rerun is cheap.
- **Cyrillic filenames** in the cache folder are expected and safe; they're
  just a readability aid, the hash suffix is what guarantees correctness.
