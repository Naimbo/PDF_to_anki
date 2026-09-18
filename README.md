# Russian 2000 Words → Anki Deck

Converts a PDF list of the 2000 most frequent Russian words (word + pronunciation +
translation + example sentence) into an importable Anki deck, with two-way cards
and optional spoken-audio pronunciation.

## Files in this folder

| File | What it does |
|---|---|
| `pdf_to_anki.py` | Basic converter: PDF → `.apkg`, text only, no audio. |
| `pdf_to_anki_audio.py` | Same, plus spoken audio for the word *and* the example sentence. |
| `test_tts_voices.py` | Generates small side-by-side audio samples so you can compare voices/settings *before* running the full conversion. |

If you only care about text (no audio), you only need `pdf_to_anki.py`. Everything
below assumes you want audio, so it's about `pdf_to_anki_audio.py`.

## What the PDF needs to look like

Each entry should follow this pattern:

```
1 - И [i] - And

Вчера она купила фрукты и овощи.
She bought some fruit and vegetables yesterday.
```

i.e. `rank - word [pronunciation] - translation`, followed by a Russian example
sentence and its English translation. The parser is line-based and tolerant of
sentences that wrap across more than one line, but it does expect this basic shape.

## What the cards look like

Each word produces **two cards**:

- **Russian → English**: front shows the word + its audio; back reveals
  `Pronunciation: [...]`, the translation, and the example sentence (with its own
  audio) plus its English translation.
- **English → Russian**: front shows the translation; back reveals the word,
  `Pronunciation: [...]`, its audio, and the example sentence + audio.

## Setup (Linux Mint / Ubuntu-based)

**1. System packages** (not Python packages — install with `apt`):
```bash
sudo apt update
sudo apt install -y python3-venv rhvoice rhvoice-russian espeak-ng ffmpeg
```
- `rhvoice` + `rhvoice-russian` — the natural-sounding Russian voice (primary engine)
- `espeak-ng` — robotic fallback, used automatically if RHVoice isn't available
- `ffmpeg` — compresses the generated audio to mp3

**2. Python virtual environment** (recommended, keeps things isolated from your system Python):
```bash
cd ~/Downloads          # or wherever you keep the scripts + PDF
python3 -m venv anki_env
source anki_env/bin/activate
pip install pdfplumber genanki
```
Re-run `source anki_env/bin/activate` each time you come back to this in a new terminal.

## Usage

**1. (Optional but recommended) Preview voices before committing:**
```bash
python3 test_tts_voices.py --voices Anna,Aleksandr-hq --rates 90,100
```
Listen to the files in `./tts_samples/` and pick your favorite voice/rate/bitrate.

**2. Generate the full deck:**
```bash
python3 pdf_to_anki_audio.py your_file.pdf -o russian_2000.apkg --csv russian_2000.csv
```

**3. Import into Anki:** File → Import... → select `russian_2000.apkg`.

## All options

### `pdf_to_anki_audio.py`

| Flag | Default | What it does |
|---|---|---|
| `pdf` (positional) | — | Path to the input PDF |
| `-o, --output` | `russian_deck.apkg` | Output `.apkg` path |
| `--deck-name` | `Russian 2000 Most Frequent Words` | Name shown in Anki |
| `--csv` | *(none)* | Also write a human-readable CSV backup |
| `--no-audio` | off | Skip audio generation entirely (text-only, like `pdf_to_anki.py`) |
| `--tts-engine` | `auto` | `auto` (prefer RHVoice, fall back to espeak), `rhvoice`, or `espeak` |
| `--voice` | `Anna` (RHVoice) / `ru` (espeak) | Voice name — see list below |
| `--rhvoice-rate` | `100` | Speech rate, % of normal. Try `90` for slightly slower/clearer. |
| `--rhvoice-pitch` | `100` | Pitch, % of normal. Best left alone. |
| `--mp3-bitrate` | `96k` | Audio quality. `48k` smaller/rougher, `128k` cleanest/largest. |
| `--tts-speed` | `140` | espeak-only: words per minute |

**RHVoice voices:** `Aleksandr-hq` (higher-quality re-recording), `Aleksandr`, `Anna`,
`Arina`, `Artemiy`, `Elena`, `Evgeniy-Rus`, `Irina`, `Mikhail`, `Pavel`, `Tatiana`,
`Victoria`, `Vitaliy`, `Yuriy`.

### `test_tts_voices.py`

Same idea, but every setting accepts a comma-separated list so you can generate
multiple variants at once: `--voices`, `--rates`, `--bitrates`, `--engine both`
(RHVoice + espeak side by side), `--words`, `--sentences` (semicolon-separated),
`--outdir`.

### `pdf_to_anki.py`

Just `pdf`, `-o/--output`, `--deck-name`, `--csv` — no audio options, since it
doesn't generate any.

## Re-running / updating a deck

Each script uses a fixed internal note-type ID, so re-running it and re-importing
into Anki updates the *same* note type and deck rather than creating duplicates —
as long as you keep using the same script version. If you edit the card templates
yourself in Anki afterward, re-importing may not overwrite your manual changes;
Anki's importer generally keeps existing cards' review history and only touches
fields/templates that changed.

## Troubleshooting

- **`No TTS engine found`** → you skipped the `apt install` step above. Run it, then
  confirm with `which RHVoice-test` and `which ffmpeg` (both should print a path).
  
- **`Missing dependency` (pdfplumber/genanki)** → activate your venv
  (`source anki_env/bin/activate`) and re-run `pip install pdfplumber genanki`.
- **Some entries missing example sentences** → the script prints a warning listing
  which ranks were affected; usually means that entry's formatting in the PDF
  didn't quite match the expected pattern. Worth checking those pages by hand.
- **Cyrillic filenames** in `test_tts_voices.py` output work fine in any modern
  Linux file manager, but if a terminal tool mangles them, that's just a display
  quirk — the files themselves are fine.

## Other ways to get more natural audio

- **HyperTTS** (Anki add-on) — lets you generate audio *after* import using
  premium neural voices (Azure, Google Cloud, ElevenLabs), with a free trial and
  some always-free voices. Good if you want the most natural voice available and
  don't mind doing audio generation from inside Anki instead of the script.
- **edge-tts** — a free Python library that uses Microsoft Edge's online neural
  voices (no API key, no cost). It's genuinely more natural than RHVoice. I've
  confirmed the CLI installs and runs, but haven't wired it into these scripts
  yet — happy to add it as another `--tts-engine` option if you want to try it.
- **Forvo** (real native-speaker recordings) — deliberately *not* implemented
  here: their terms don't allow caching/bulk-downloading audio, which conflicts
  with baking permanent audio into a deck. See the AwesomeTTS/HyperTTS add-ons
  or forvo.com directly for occasional individual lookups instead.

## Known assumptions worth knowing about

- **Capitalization**: list headers capitalize the first letter of every word
  (e.g. "И", "Не", "Он"). The script lowercases a word only when the example
  sentence proves the lowercase form is actually used elsewhere; otherwise it's
  left as-is (this correctly preserves genuine proper nouns like "Россия", but
  means a handful of ambiguous pronouns/function words that only ever appear
  sentence-initially in their example might stay capitalized when they shouldn't).
  Worth a quick manual scan of the CSV output for anything that looks off.
