#!/usr/bin/env python3
"""
test_edge_tts_russian.py

Generate a small set of Russian MP3 samples with Microsoft Edge TTS so you
can compare voice quality before integrating it into your Anki/PDF script.

Install:
    python3 -m pip install edge-tts

Run:
    python3 test_edge_tts_russian.py

Output:
    ./edge_tts_samples/

Note:
    edge-tts uses Microsoft's online speech service, so an internet
    connection is required.
"""

import asyncio
from pathlib import Path

import edge_tts


# ---------------------------------------------------------------------------
# Sample data based on your PDF example
# ---------------------------------------------------------------------------

ENTRIES = [
    {
        "rank": 1,
        "word": "и",
        "translation": "and",
        "sentence": "Вчера она купила фрукты и овощи.",
    },
    {
        "rank": 2,
        "word": "в",
        "translation": "in, on, at",
        "sentence": "Маленький котёнок спрятался в подвале.",
    },
    {
        "rank": 3,
        "word": "не",
        "translation": "not, no",
        "sentence": "Я не люблю дождливую погоду.",
    },
    {
        "rank": 4,
        "word": "он",
        "translation": "he",
        "sentence": "Он очень хороший друг.",
    },
]


# Two Russian neural voices so you can compare them.
VOICES = {
    "svetlana": "ru-RU-SvetlanaNeural",
    "dmitry": "ru-RU-DmitryNeural",
}


# You can change these later.
RATE = "+0%"
PITCH = "+0Hz"

OUTPUT_DIR = Path("edge_tts_samples")


async def synthesize(text: str, output_file: Path, voice: str) -> None:
    """Generate one MP3 file with Edge TTS."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=RATE,
        pitch=PITCH,
    )
    await communicate.save(str(output_file))


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total = len(ENTRIES) * len(VOICES) * 2
    done = 0

    print(f"Generating {total} MP3 files in: {OUTPUT_DIR.resolve()}")
    print()

    for short_voice_name, voice in VOICES.items():
        print(f"Voice: {voice}")

        for entry in ENTRIES:
            rank = entry["rank"]

            # Isolated vocabulary word
            word_file = OUTPUT_DIR / (
                f"{rank:02d}_{short_voice_name}_word_{entry['word']}.mp3"
            )

            print(f"  [{rank}] word     -> {word_file.name}")
            await synthesize(entry["word"], word_file, voice)
            done += 1

            # Full example sentence
            sentence_file = OUTPUT_DIR / (
                f"{rank:02d}_{short_voice_name}_sentence.mp3"
            )

            print(f"  [{rank}] sentence -> {sentence_file.name}")
            await synthesize(entry["sentence"], sentence_file, voice)
            done += 1

        print()

    print(f"Done. Generated {done} MP3 files.")
    print(f"Open this folder to listen:")
    print(f"  {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
