# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "kokoro-onnx==0.5.*",
#     "onnxruntime",
#     "soundfile",
#     "numpy",
# ]
# ///
"""Speak the voice-test lines with Kokoro-82M on the CPU, timing each line.

uv run --script tools/bench/tts/kokoro_tts.py OUT_DIR [--voices af_heart bm_george] [--threads 2]

Note: kokoro-onnx phonemizes through espeak-ng (GPL) for some words. Fine for a
local test; the product must not bundle it (see the TTS research notes).
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import soundfile as sf

FILES = {
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--lines", type=Path, default=Path("tools/spike/voice_lines.txt"))
    parser.add_argument(
        "--voices", nargs="+", default=["af_heart", "am_michael", "bm_george", "bf_emma"]
    )
    parser.add_argument(
        "--threads", type=int, default=2, help="CPU threads, to leave the game room"
    )
    args = parser.parse_args()

    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    models = (
        Path(os.environ.get("TYCHO_MODELS", Path(os.environ["LOCALAPPDATA"]) / "tycho" / "models"))
        / "kokoro"
    )
    models.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        if not (models / name).exists():
            print(f"downloading {name}...", flush=True)
            urllib.request.urlretrieve(url, models / name)  # noqa: S310 - fixed https URL

    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    session = ort.InferenceSession(
        str(models / "kokoro-v1.0.onnx"), options, providers=["CPUExecutionProvider"]
    )
    kokoro = Kokoro.from_session(session, str(models / "voices-v1.0.bin"))

    lines = [
        l.strip()
        for l in args.lines.read_text("utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
    kokoro.create("Warming up.", voice=args.voices[0], lang="en-us")  # first call pays for setup
    for voice in args.voices:
        folder = args.out / f"kokoro-{voice}"
        folder.mkdir(parents=True, exist_ok=True)
        old = folder / "timings.json"
        done = {t["line"]: t for t in json.loads(old.read_text())["lines"]} if old.exists() else {}
        timings = []
        for i, line in enumerate(lines, start=1):
            if i in done and (folder / f"{i:02d}.ogg").exists():
                timings.append(done[i])
                continue
            lang = "en-gb" if voice.startswith("b") else "en-us"
            started = time.perf_counter()
            samples, rate = kokoro.create(line, voice=voice, lang=lang)
            ms = (time.perf_counter() - started) * 1000
            sf.write(folder / f"{i:02d}.ogg", samples, rate, format="OGG", subtype="VORBIS")
            seconds = len(samples) / rate
            timings.append({"line": i, "text": line, "ms": round(ms), "audio_s": round(seconds, 2)})
            print(f"{voice:10} {i:02d} {ms:6.0f} ms for {seconds:4.1f} s of audio", flush=True)
        (folder / "timings.json").write_text(
            json.dumps(
                {"engine": "kokoro", "voice": voice, "threads": args.threads, "lines": timings},
                indent=1,
            )
        )


if __name__ == "__main__":
    sys.exit(main())
