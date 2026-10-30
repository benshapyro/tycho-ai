"""Record push-to-talk test phrases for the latency spike (issue #1).

Usage: uv run python tools/spike/record.py [--phrases FILE] [--out DIR] [--bind BINDING]

Binds push-to-talk by asking you to press any keyboard key, mouse button or
joystick button, then prompts you through each phrase. Clips are saved as
16 kHz mono WAV files with a manifest.jsonl, under recordings/ (gitignored).
"""

# soundfile ships no type information.
# pyright: reportUnknownMemberType=false, reportMissingTypeStubs=false

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import soundfile as sf

from tycho.audio.clip import Samples
from tycho.audio.microphone import SAMPLE_RATE, Microphone
from tycho.ptt import Binding, DeviceKind, Edge, InputSource, PushToTalk, capture_binding
from tycho.ptt import windows as win
from tycho.ptt.joystick import JoystickSource

POLL_SECONDS = 0.005
TAIL_SECONDS = 0.15  # keep recording briefly after release so the last word isn't clipped
MIN_CLIP_SECONDS = 0.3
ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    args = _parse_args()
    phrases = _load_phrases(args.phrases)
    keyboard_mouse, sticks = win.KeyboardMouseSource(), JoystickSource()
    sources: list[InputSource] = [keyboard_mouse, sticks]

    binding = Binding.parse(args.bind) if args.bind else _ask_for_binding(sources)
    label = (
        sticks.describe(binding) if binding.kind is DeviceKind.JOYSTICK else win.describe(binding)
    )
    print(f"\nPush-to-talk: {label}  ({binding})")
    print("Reuse it next time with --bind " + str(binding))

    out = args.out or ROOT / "recordings" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    ptt = PushToTalk(binding, sources)

    with Microphone(device=args.device) as mic, (out / "manifest.jsonl").open("a") as manifest:
        print(f"Microphone: {mic.name}\nSaving to: {out}\n")
        index = 0
        while index < len(phrases):
            phrase = phrases[index]
            print(f'[{index + 1}/{len(phrases)}]  Hold {label} and say:  "{phrase}"')
            clip, held_seconds = _record_one(ptt, mic)
            seconds = len(clip) / SAMPLE_RATE
            if held_seconds < MIN_CLIP_SECONDS:
                print("    Too short. Try again.\n")
                continue
            print(f"    {seconds:.1f}s captured.  Enter = keep, r = redo, q = quit: ", end="")
            choice = input().strip().lower()
            if choice == "r":
                continue
            if choice == "q":
                break
            name = f"{index + 1:03d}.wav"
            sf.write(out / name, clip, SAMPLE_RATE, subtype="PCM_16")
            record = {
                "file": name,
                "phrase": phrase,
                "seconds": round(seconds, 3),
                "held_seconds": round(held_seconds, 3),
                "binding": str(binding),
                "microphone": mic.name,
                "sample_rate": SAMPLE_RATE,
            }
            manifest.write(json.dumps(record) + "\n")
            manifest.flush()
            index += 1
        if mic.overflows:
            print(f"\nWarning: {mic.overflows} audio overflow(s); some audio may be missing.")
    print(f"\nDone. {index} clip(s) in {out}")


def _record_one(ptt: PushToTalk, mic: Microphone) -> tuple[Samples, float]:
    while ptt.poll() is not Edge.PRESSED:
        time.sleep(POLL_SECONDS)
    mic.start()
    pressed_at = time.perf_counter()
    while ptt.poll() is not Edge.RELEASED:
        time.sleep(POLL_SECONDS)
    held = time.perf_counter() - pressed_at
    time.sleep(TAIL_SECONDS)
    return mic.stop(), held


def _ask_for_binding(sources: list[InputSource]) -> Binding:
    print("Press the button you want for push-to-talk:")
    print("any keyboard key, mouse button (not left click), or joystick button.")
    found = capture_binding(sources, ignore=frozenset({win.LEFT_MOUSE}), timeout=60)
    if found is None:
        raise SystemExit("No button pressed within 60 seconds.")
    return found


def _load_phrases(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--phrases", type=Path, default=Path(__file__).with_name("phrases.txt"))
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--bind", help="reuse a binding, e.g. mouse:0x05")
    parser.add_argument("--device", help="microphone name or index (default: system default)")
    return parser.parse_args()


if __name__ == "__main__":
    main()
