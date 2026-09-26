# pdf_to_anki_edge

Convert a Russian frequency-list PDF into an Anki deck with TTS audio.

Each vocabulary entry becomes **two** Anki cards (RU→EN and EN→RU), and each
card carries three audio clips: the word itself, the example sentence at
normal speed, and the example sentence slowed down. Russian TTS is generated
online via Microsoft Edge's neural voices by default, with RHVoice and
eSpeak NG available as offline alternatives.

---

## Table of contents

- [What it produces](#what-it-produces)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Command-line reference](#command-line-reference)
  - [Selecting which words to include](#selecting-which-words-to-include)
  - [Speed control](#speed-control)
  - [TTS engines and voices](#tts-engines-and-voices)
  - [Caching](#caching)
  - [Debugging and validation](#debugging-and-validation)
- [Understanding the PDF parser](#understanding-the-pdf-parser)
- [Understanding the warnings](#understanding-the-warnings)
- [Workflow recipes](#workflow-recipes)
- [Troubleshooting](#troubleshooting)
- [How it works (internals)](#how-it-works-internals)
- [Self-tests](#self-tests)
- [Changelog](#changelog)
- [License](#license)

---

## What it produces

A single `.apkg` file you open in Anki (`File → Import`). For each entry in
the PDF you get:

**Card 1 — Russian → English**
- Front: the Russian word + word audio
- Back: pronunciation, English translation, the Russian example sentence
  (with the target word bolded), a 🐢 slow clip and a 🐇 normal clip of the
  sentence, the English translation of the sentence, and the frequency rank.

**Card 2 — English → Russian**
- Front: the English translation
- Back: the Russian word, pronunciation, word audio, the same example block,
  and the frequency rank.

Optional `--csv` writes a plain-text backup of everything the deck contains,
which is useful for diffing between runs or importing elsewhere.

---

## Requirements

- **Python 3.9+** (uses `tuple[str, str] | tuple[None, None]` type syntax and
  `list[dict]` generics)
- **Python packages:** `pdfplumber`, `genanki`, `edge-tts`
- **Optional system packages:**
  - `ffmpeg` — required if you use RHVoice or eSpeak NG (not needed for Edge)
  - `rhvoice` + `rhvoice-russian` — if you want the RHVoice backend
  - `espeak-ng` — if you want the eSpeak NG backend
- **Internet connection** — only for the default Edge TTS backend

---

## Installation

### Linux (Debian/Ubuntu/Mint)

```bash
python3 -m pip install pdfplumber genanki edge-tts

# Optional offline TTS backends:
sudo apt install ffmpeg rhvoice rhvoice-russian    # for RHVoice
sudo apt install ffmpeg espeak-ng                  # for eSpeak NG
