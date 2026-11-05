"""Test the Tile's window before building it (#2): does a see-through card stay on top of
Star Citizen, keep the game focused, get along with Easy Anti-Cheat, and how much memory
does it take?

    uv run python tools/spike/tile/tile_window.py                 # tkinter, about 2 minutes
    uv run python tools/spike/tile/tile_window.py --engine qt     # Qt WebEngine: the real look
    uv run python tools/spike/tile/tile_window.py --seconds 600   # a longer play session

Start it, then switch to the game and play as usual. A sample card cycles in the corner.
Afterwards it asks two questions only you can answer, prints the results and saves them
to Tycho's logs folder (tile-test-<engine>-<time>.json).

Without the game running, it still checks focus, click-through and memory on the desktop.
The engines run in their own process; this one only watches. Nothing here touches the
game: no injection, no reading its memory, no keys sent.
"""

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import tile_win32 as win32

from tycho import paths
from tycho.input.windows import GAME_EXECUTABLE
from tycho.stt.device import smi_path

HERE = Path(__file__).parent
TICK = 0.5


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--engine", choices=["tk", "qt"], default="tk")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--corner", choices=["tr", "tl", "br", "bl"], default="tr")
    parser.add_argument("--no-questions", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    game = win32.running(GAME_EXECUTABLE)
    if game:
        print("Star Citizen is running: switch to it now and play.")
    else:
        print("Star Citizen isn't running: desktop checks only.")
    before = _vram_mb()
    started = time.monotonic()
    engine = _start(args.engine, args.seconds, args.corner)
    hwnd = _read_hwnd(engine)
    if hwnd is None:
        print("The engine didn't open its window:")
        print(engine.stderr.read() if engine.stderr else "")
        return 1
    print(f"Card shown ({args.engine}, {time.monotonic() - started:.1f} s to open). Watching...")

    focus_taken = not_click_through = covered = lost_topmost = samples = 0
    fronts: Counter[str] = Counter()
    above: Counter[str] = Counter()
    vram_peak = ram_peak = 0.0
    game_lost = False
    last_slow = 0.0
    while engine.poll() is None and win32.is_window(hwnd):
        samples += 1
        ours = set(win32.tree(engine.pid))
        front = win32.foreground()
        if front:
            pid = win32.window_pid(front)
            if pid in ours:
                focus_taken += 1
            fronts[win32.executable(pid) or "?"] += 1
        area = win32.rect(hwnd)
        if win32.window_pid(win32.window_at(*area.centre)) in ours:
            not_click_through += 1
        if not win32.ex_style(hwnd) & win32.WS_EX_TOPMOST:
            lost_topmost += 1
        over = [w for w in win32.windows_above(hwnd, area) if win32.window_pid(w) not in ours]
        if over:
            covered += 1
            above.update(win32.executable(win32.window_pid(w)) or "?" for w in over)
        now = time.monotonic()
        if now - last_slow >= 2:
            last_slow = now
            vram_peak = max(vram_peak, _vram_mb() - before)
            ram_peak = max(ram_peak, sum(win32.working_set_mb(p) for p in ours))
            if game and not win32.running(GAME_EXECUTABLE):
                game_lost = True
        time.sleep(TICK)
    engine.wait(10)

    answers: dict[str, bool | None] = {"saw_card": None, "anti_cheat_trouble": None}
    if sys.stdin.isatty() and not args.no_questions:
        where = "over the game the whole time" if game else "in the corner"
        answers["saw_card"] = _ask(f"Did you see the card {where}?")
        if game:
            answers["anti_cheat_trouble"] = _ask(
                "Did the game warn, kick you, or crash (Easy Anti-Cheat)?"
            )

    result: dict[str, object] = {
        "engine": args.engine,
        "game_running": game,
        "seconds": round(time.monotonic() - started),
        "samples": samples,
        "focus_taken": focus_taken,
        "foreground": dict(fronts.most_common(5)),
        "not_click_through": not_click_through,
        "lost_topmost": lost_topmost,
        "covered_samples": covered,
        "windows_above": dict(above.most_common(5)),
        "game_closed_during_test": game_lost,
        "vram_mb": round(vram_peak) if before else None,
        "ram_mb": round(ram_peak),
        **answers,
    }
    out = paths.logs_dir() / f"tile-test-{args.engine}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), "utf-8")
    _report(result)
    print(f"\nSaved to {out}")
    return 0


def _start(engine: str, seconds: float, corner: str) -> subprocess.Popen[str]:
    rest = ["--seconds", str(seconds), "--corner", corner]
    if engine == "tk":
        command = [sys.executable, str(HERE / "engine_tk.py"), *rest]
    else:
        uv = shutil.which("uv")
        if uv is None:
            raise SystemExit("The Qt engine needs uv on PATH")
        print("Starting Qt (the first run downloads PySide6 into uv's cache, about 400 MB)...")
        command = [uv, "run", "--script", str(HERE / "engine_qt.py"), *rest]
    return subprocess.Popen(  # noqa: S603 - our own scripts, fixed arguments
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _read_hwnd(engine: subprocess.Popen[str], timeout: float = 600) -> int | None:
    """The engine prints "HWND <n>" once its window is up. Read it without blocking forever."""
    found: list[int] = []

    def read() -> None:
        assert engine.stdout is not None  # noqa: S101 - set by Popen(stdout=PIPE)
        for line in engine.stdout:
            if line.startswith("HWND "):
                found.append(int(line.split()[1]))
                return

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    return found[0] if found else None


def _vram_mb() -> float:
    """The whole card's memory in use: on Windows nvidia-smi can't split it by process."""
    smi = smi_path()
    if smi is None:
        return 0.0
    out = subprocess.run(  # noqa: S603 - nvidia-smi from the driver's folder
        [str(smi), "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(out.stdout.split()[0])
    except IndexError, ValueError:
        return 0.0


def _ask(question: str) -> bool | None:
    reply = input(f"{question} [y/n] ").strip().lower()
    return True if reply.startswith("y") else False if reply.startswith("n") else None


def _report(r: dict[str, object]) -> None:
    samples = r["samples"] or 1

    def count(key: str) -> int:
        n = r[key]
        return n if isinstance(n, int) else 0

    def flag(key: str) -> bool | None:
        v = r[key]
        return v if isinstance(v, bool) else None

    def verdict(ok: bool | None) -> str:
        return "unknown" if ok is None else "PASS" if ok else "FAIL"

    def line(label: str, value: str, note: str = "") -> None:
        print(f"  {label:<22}{value}" + (f"  ({note})" if note else ""))

    def check(label: str, key: str, what: str) -> None:
        line(label, verdict(count(key) == 0), f"{what} in {count(key)}/{samples} samples")

    game = "on" if r["game_running"] else "off"
    print(f"\nTile test: {r['engine']}, {r['seconds']} s, game {game}")
    check("Never took focus", "focus_taken", "took it")
    check("Clicks pass through", "not_click_through", "blocked")
    check("Stayed on top", "lost_topmost", "lost it")
    line(
        "Windows above it", str(r["windows_above"] or "none"), f"{count('covered_samples')} samples"
    )
    line("You saw it", verdict(flag("saw_card")))
    if r["game_running"]:
        trouble = flag("anti_cheat_trouble")
        line("No anti-cheat trouble", verdict(None if trouble is None else not trouble))
        line("Game stayed open", verdict(not r["game_closed_during_test"]))
    vram = r["vram_mb"]
    line("Graphics memory", "n/a" if vram is None else f"+{vram} MB", "whole card, noisy")
    line("Memory (RAM)", f"{r['ram_mb']} MB", "the window's processes")
    line("In front most often", str(r["foreground"]))


if __name__ == "__main__":
    sys.exit(main())
