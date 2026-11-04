"""How much graphics memory the speech model takes, and how fast it is, with ONNX
Runtime's default CUDA settings and with Tycho's lean ones (src/tycho/stt/parakeet.py).

    uv run python tools/bench/gpu_memory.py recordings/<session>
    uv run python tools/bench/gpu_memory.py recordings/<session> --with-game   # game open

Each setting runs in a process of its own, so one can't hold memory for the other. The
peak is read from nvidia-smi while it loads and transcribes every clip twice: the card's
use less what it was before this process loaded anything, so other programs changing
their use at the same time add noise.

It refuses to run while Star Citizen is open unless --with-game is given: it loads the
graphics card, which is the point of that run but not of an idle one.
"""

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

VARIANTS = ("default", "lean")


def main() -> int:
    parser = argparse.ArgumentParser(description="Graphics memory and speed of the speech model.")
    parser.add_argument("session", type=Path, help="a folder of 16 kHz mono WAV clips")
    parser.add_argument("--with-game", action="store_true", help="run with Star Citizen open")
    parser.add_argument("--child", choices=VARIANTS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    clips = sorted(args.session.glob("*.wav"))
    if not clips:
        raise SystemExit(f"No .wav clips in {args.session}")
    if args.child:
        print(json.dumps(_measure(args.child, clips)))
        return 0
    if _game_running() and not args.with_game:
        raise SystemExit("Star Citizen is running. Close it, or pass --with-game to measure that.")
    results: dict[str, dict[str, object]] = {}
    for variant in VARIANTS:
        out = subprocess.run(  # noqa: S603 - this script, with fixed arguments
            [sys.executable, __file__, str(args.session), "--child", variant],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [line for line in out.stdout.splitlines() if line.startswith("{")]
        if out.returncode or not lines:
            print(f"{variant}: failed\n{out.stderr[-2000:]}")
            continue
        results[variant] = json.loads(lines[-1])
    _report(results, len(clips), args.with_game)
    return 0


def _measure(variant: str, clips: list[Path]) -> dict[str, object]:
    from tycho import paths
    from tycho.stt import CudaLibraries, cuda_available
    from tycho.stt.models import PARAKEET_V2, ModelStore
    from tycho.stt.parakeet import LEAN_CUDA, ParakeetTranscriber

    # As tycho run does: this also loads NVIDIA's libraries, without which ONNX Runtime
    # quietly uses the processor.
    libraries = CudaLibraries(paths.cuda_dir())
    if not cuda_available(libraries.dlls if libraries.ready() else None):
        raise SystemExit("CUDA isn't available here: run `tycho gpu install` first")
    folder = ModelStore(paths.models_dir()).folder(PARAKEET_V2)
    peak = _Peak()
    before = peak.now()
    peak.start()
    started = time.perf_counter()
    options: dict[str, str] = dict(LEAN_CUDA) if variant == "lean" else {}
    transcriber = ParakeetTranscriber._open(folder, "gpu", options)  # pyright: ignore[reportPrivateUsage]
    transcriber._warm_up()  # pyright: ignore[reportPrivateUsage]
    loaded = time.perf_counter() - started
    after_load = peak.now()
    audio = [_read(clip) for clip in clips]
    times: list[float] = []
    texts: list[str] = []
    for _ in range(2):  # the second pass is what a session looks like once it's warm
        texts = []
        for samples in audio:
            t = time.perf_counter()
            texts.append(transcriber.transcribe(samples, 16_000))
            times.append((time.perf_counter() - t) * 1000)
    peak.stop()
    warm = times[len(audio) :]
    return {
        "device": transcriber.device,
        "load_s": round(loaded, 1),
        "p50_ms": round(statistics.median(warm)),
        "p90_ms": round(statistics.quantiles(warm, n=10)[-1]),
        "after_load_mb": after_load - before,
        "peak_mb": peak.most - before,
        "texts": texts,
    }


class _Peak(threading.Thread):
    """The graphics card's memory in use, read from nvidia-smi every 0.2 s."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.most = 0
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            self.most = max(self.most, self.now())
            time.sleep(0.2)

    def stop(self) -> None:
        self._stop.set()
        self.join(2)
        self.most = max(self.most, self.now())

    def now(self) -> int:
        from tycho.stt.device import smi_path

        smi = smi_path()
        if smi is None:
            return 0
        # The whole card's use: on Windows, nvidia-smi can't say how much one process
        # uses ("[N/A]"), so the model's share is this less what was in use before.
        out = subprocess.run(  # noqa: S603 - nvidia-smi from the driver's folder
            [str(smi), "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            return int(out.stdout.split()[0])
        except IndexError, ValueError:
            return 0


def _read(path: Path) -> np.ndarray[tuple[int], np.dtype[np.float32]]:
    with wave.open(str(path), "rb") as clip:
        if clip.getframerate() != 16_000 or clip.getsampwidth() != 2 or clip.getnchannels() != 1:
            raise SystemExit(f"{path} isn't 16 kHz, 16-bit mono")
        frames = clip.readframes(clip.getnframes())
    return (np.frombuffer(frames, dtype=np.int16) / np.float32(32768)).astype(np.float32)


def _game_running() -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq StarCitizen.exe", "/NH"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return "StarCitizen.exe" in out.stdout


def _report(results: dict[str, dict[str, object]], clips: int, with_game: bool) -> None:
    print(f"\n{clips} clips, each transcribed twice{' (game open)' if with_game else ''}\n")
    print(f"{'setting':8} {'device':6} {'load':>6} {'p50':>7} {'p90':>7} {'loaded':>9} {'peak':>9}")
    for name, r in results.items():
        print(
            f"{name:8} {r['device']!s:6} {r['load_s']!s:>5}s {r['p50_ms']!s:>5}ms "
            f"{r['p90_ms']!s:>5}ms {r['after_load_mb']!s:>6} MB {r['peak_mb']!s:>6} MB"
        )
    if len(results) == 2:
        same = results["default"]["texts"] == results["lean"]["texts"]
        print(f"\nSame transcripts: {'yes' if same else 'NO'}")


if __name__ == "__main__":
    sys.exit(main())
