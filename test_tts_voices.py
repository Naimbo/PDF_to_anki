#!/usr/bin/env python3
"""
test_tts_voices.py

Generates small side-by-side audio samples across different RHVoice (or
espeak-ng) voices, speech rates, and mp3 bitrates, so you can actually listen
and compare before committing to settings for the full pdf_to_anki_audio.py
run on your 2000-word deck.

Requirements (system packages, not pip):
    sudo apt-get install rhvoice rhvoice-russian espeak-ng ffmpeg

Usage examples:
    # Compare ALL installed RHVoice voices on the built-in sample words/sentences
    python3 test_tts_voices.py

    # Compare just a few voices you're curious about
    python3 test_tts_voices.py --voices Anna,Aleksandr-hq,Irina

    # Try different speech rates on one voice you like
    python3 test_tts_voices.py --voices Aleksandr-hq --rates 80,90,100,110

    # Compare mp3 bitrates (quality vs file size)
    python3 test_tts_voices.py --voices Anna --bitrates 48k,96k,128k

    # Compare RHVoice vs espeak-ng directly
    python3 test_tts_voices.py --engine both --voices Anna

    # Use your own words/sentences instead of the built-in sample
    python3 test_tts_voices.py --words "привет,спасибо,хорошо" \\
        --sentences "Привет, как дела?;Спасибо большое за помощь."

Output: one small mp3 per combination, in ./tts_samples/ (or --outdir),
named so you can tell exactly what settings produced it. Just double-click
files in your file manager to play them.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# A handful of words + sentences from the earlier examples - enough variety
# (short/long, common pronoun, a proper noun) to hear real differences.
DEFAULT_WORDS = ["и", "не", "он", "россия", "хорошо"]
DEFAULT_SENTENCES = [
    "Вчера она купила фрукты и овощи.",
    "Я не люблю дождливую погоду.",
    "Россия очень большая страна с богатой историей и культурой.",
]

ALL_RHVOICE_VOICES = [
    "Aleksandr-hq", "Aleksandr", "Anna", "Arina", "Artemiy", "Elena",
    "Evgeniy-Rus", "Irina", "Mikhail", "Pavel", "Tatiana", "Victoria",
    "Vitaliy", "Yuriy",
]


def slugify(text: str) -> str:
    text = re.sub(r'[^\w]+', '_', text.strip().lower())
    return text.strip('_')[:40] or "x"


def wav_to_mp3(wav_path: Path, out_mp3: Path, bitrate: str) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(out_mp3)],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and out_mp3.exists()


def synth_rhvoice(text: str, out_mp3: Path, voice: str, rate: int, pitch: int, bitrate: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
        f.write(text)
        txt_path = Path(f.name)
    wav_path = txt_path.with_suffix(".wav")
    try:
        r = subprocess.run(
            ["RHVoice-test", "-p", voice, "-R", "24000", "-r", str(rate), "-t", str(pitch),
             "-i", str(txt_path), "-o", str(wav_path)],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            print(f"  ! RHVoice failed (voice={voice}): {r.stderr.strip()[:200]}")
            return False
        return wav_to_mp3(wav_path, out_mp3, bitrate)
    except subprocess.TimeoutExpired:
        print(f"  ! RHVoice timed out (voice={voice})")
        return False
    finally:
        txt_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


def synth_espeak(text: str, out_mp3: Path, speed: int, bitrate: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = Path(f.name)
    try:
        r = subprocess.run(
            ["espeak-ng", "-v", "ru", "-s", str(speed), "-w", str(wav_path), text],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            print(f"  ! espeak-ng failed: {r.stderr.strip()[:200]}")
            return False
        return wav_to_mp3(wav_path, out_mp3, bitrate)
    finally:
        wav_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Generate side-by-side TTS samples to compare voices/settings.")
    parser.add_argument("--engine", choices=["rhvoice", "espeak", "both"], default="rhvoice")
    parser.add_argument("--voices", default=None,
                         help="Comma-separated RHVoice voice names to test (default: all installed). "
                              f"Options: {', '.join(ALL_RHVOICE_VOICES)}")
    parser.add_argument("--rates", default="100", help="Comma-separated RHVoice rate %% values to test (default: 100)")
    parser.add_argument("--pitch", type=int, default=100, help="RHVoice pitch %% (default: 100)")
    parser.add_argument("--bitrates", default="96k", help="Comma-separated mp3 bitrates to test (default: 96k)")
    parser.add_argument("--words", default=None, help="Comma-separated words to test (default: built-in sample)")
    parser.add_argument("--sentences", default=None,
                         help="Semicolon-separated sentences to test (default: built-in sample)")
    parser.add_argument("--outdir", default="tts_samples", help="Output folder (default: ./tts_samples)")
    parser.add_argument("--espeak-speed", type=int, default=140, help="espeak-ng words/min (default: 140)")
    args = parser.parse_args()

    voices = [v.strip() for v in args.voices.split(",")] if args.voices else ALL_RHVOICE_VOICES
    rates = [int(r) for r in args.rates.split(",")]
    bitrates = [b.strip() for b in args.bitrates.split(",")]
    words = [w.strip() for w in args.words.split(",")] if args.words else DEFAULT_WORDS
    sentences = [s.strip() for s in args.sentences.split(";")] if args.sentences else DEFAULT_SENTENCES

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found. Install with: sudo apt-get install ffmpeg")

    engines_to_run = []
    if args.engine in ("rhvoice", "both"):
        if shutil.which("RHVoice-test") is None:
            print("Warning: RHVoice-test not found, skipping rhvoice. "
                  "Install with: sudo apt-get install rhvoice rhvoice-russian")
        else:
            engines_to_run.append("rhvoice")
    if args.engine in ("espeak", "both"):
        if shutil.which("espeak-ng") is None:
            print("Warning: espeak-ng not found, skipping espeak. Install with: sudo apt-get install espeak-ng")
        else:
            engines_to_run.append("espeak")

    if not engines_to_run:
        sys.exit("No usable TTS engine found.")

    manifest = []  # (ok, filename)

    def run_batch(label: str, synth_fn) -> None:
        for w in words:
            fname = f"word_{slugify(w)}__{label}.mp3"
            ok = synth_fn(w, outdir / fname)
            manifest.append((ok, fname))
        for s in sentences:
            fname = f"sentence_{slugify(s)}__{label}.mp3"
            ok = synth_fn(s, outdir / fname)
            manifest.append((ok, fname))

    if "rhvoice" in engines_to_run:
        for voice in voices:
            for rate in rates:
                for bitrate in bitrates:
                    label = f"rhvoice_{voice}_r{rate}_{bitrate}"
                    print(f"Generating: {label} ...")
                    run_batch(label, lambda text, path, v=voice, r=rate, b=bitrate:
                              synth_rhvoice(text, path, voice=v, rate=r, pitch=args.pitch, bitrate=b))

    if "espeak" in engines_to_run:
        for bitrate in bitrates:
            label = f"espeak_s{args.espeak_speed}_{bitrate}"
            print(f"Generating: {label} ...")
            run_batch(label, lambda text, path, b=bitrate:
                      synth_espeak(text, path, speed=args.espeak_speed, bitrate=b))

    ok_count = sum(1 for ok, _ in manifest if ok)
    print(f"\nDone: {ok_count}/{len(manifest)} clips written to ./{outdir}/")
    print("Filename pattern: {word|sentence}_{text}__{engine}_{voice}_{rate}_{bitrate}.mp3")
    print("Open the folder in your file manager and double-click to play/compare.\n")

    failed = [fname for ok, fname in manifest if not ok]
    if failed:
        print(f"{len(failed)} clip(s) failed to generate:")
        for fname in failed:
            print(f"  {fname}")


if __name__ == "__main__":
    main()
