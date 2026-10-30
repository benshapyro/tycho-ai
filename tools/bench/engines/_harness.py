"""Shared timing loop for speech-to-text engine scripts.

Each engine script runs in its own isolated environment (PEP 723 metadata), so
this file uses only the standard library plus numpy and soundfile, which every
engine declares. Results are one JSON line per clip.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

type Transcribe = Callable[[np.ndarray], str]


def parse_args(default_model: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("clips", type=Path, help="a recordings/<session> folder")
    parser.add_argument("--model", default=default_model)
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--label", help="name in results; defaults to the model name")
    parser.add_argument("--runs", type=int, default=3, help="timed runs per clip; median is kept")
    return parser.parse_args()


def models_dir() -> Path:
    """Where model files go: $ULTIMO_MODELS, else %LOCALAPPDATA%/ultimo/models.

    Models are downloaded as plain files rather than through the Hugging Face
    cache, whose links ONNX Runtime refuses to follow.
    """
    default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ultimo" / "models"
    return Path(os.environ.get("ULTIMO_MODELS", default))


def run(args: argparse.Namespace, engine: str, load: Callable[[], Transcribe]) -> None:
    rows = [json.loads(line) for line in (args.clips / "manifest.jsonl").read_text().splitlines()]
    label = args.label or f"{engine}:{args.model}:{args.device}"
    out_dir = args.clips / "results"
    out_dir.mkdir(exist_ok=True)

    vram_before = _vram_used_mb()
    started = time.perf_counter()
    transcribe = load()
    load_seconds = time.perf_counter() - started

    first = _read(args.clips / rows[0]["file"])
    transcribe(first)  # warm-up: first call pays for kernel compilation and allocation
    vram_after = _vram_used_mb()

    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in label)
    with (out_dir / f"{safe}.jsonl").open("w", encoding="utf-8") as out:
        meta = {
            "type": "meta",
            "label": label,
            "engine": engine,
            "model": args.model,
            "device": args.device,
            "load_seconds": round(load_seconds, 2),
            "vram_mb": None if vram_before is None else vram_after - vram_before,  # pyright: ignore[reportOptionalOperand]
            "game_running": _game_running(),
            "machine": platform.node(),
        }
        out.write(json.dumps(meta) + "\n")
        for row in rows:
            audio = _read(args.clips / row["file"])
            timings: list[float] = []
            text = ""
            for _ in range(args.runs):
                t0 = time.perf_counter()
                text = transcribe(audio)
                timings.append(time.perf_counter() - t0)
            record = {
                "type": "clip",
                "file": row["file"],
                "phrase": row["phrase"],
                "text": text.strip(),
                "audio_seconds": row["seconds"],
                "ms": round(sorted(timings)[len(timings) // 2] * 1000, 1),
            }
            out.write(json.dumps(record) + "\n")
            print(f"{record['ms']:>7.0f} ms  {row['phrase']!r:55} -> {record['text']!r}")
    print(f"\n{label}: loaded in {load_seconds:.1f}s, results in {out_dir}")


def _read(path: Path) -> np.ndarray:
    audio, rate = sf.read(path, dtype="float32")
    if rate != 16_000:
        raise SystemExit(f"{path} is {rate} Hz; expected 16000")
    return audio


def _vram_used_mb() -> int | None:
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    out = subprocess.run(  # noqa: S603
        [smi, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(out.stdout.split()[0])
    except IndexError, ValueError:
        return None


def _game_running() -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq StarCitizen.exe", "/NH"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return "StarCitizen.exe" in out.stdout
