"""End-to-end self-test through Windows itself, without the game.

    uv run python tools/spike/selftest.py [--clips 30]

Everything is real except the microphone and the game:
- push-to-talk is a real (injected) F24 press, read by the real button poller;
- your recordings are fed in real time, in 20 ms blocks, as the microphone would;
- real Parakeet, lexicon, matcher, safety gate and your real keybinds;
- real SendInput key presses, guarded by the real focus check;
- a stand-in window plays the game's part: the focus check accepts it instead of
  StarCitizen.exe, and a low-level keyboard hook records exactly which scan
  codes reached Windows, to compare with what each command should press.

It also checks that nothing is pressed when the stand-in isn't in front, and
that a key held mid-press is released when push-to-talk is pressed again.
Nothing is pressed in any other window: the focus check sees to that.

Run it when nobody is using the PC: it brings its window to the front for each
clip, and stops by itself if someone types or another window keeps the front.
The keyboard hook keeps only injected keys; for real typing it notes the time alone.
"""

# pyright: reportUnknownMemberType=false, reportMissingTypeStubs=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import argparse
import asyncio
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import tomllib
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

import numpy as np
import soundfile as sf

from tycho import app, settings
from tycho.audio.clip import ClipBuffer, Samples
from tycho.commands import CommandMatcher, resolve
from tycho.feedback import SoundDevicePlayer, SoundFeedback
from tycho.game.keybinds import Keybinds
from tycho.input import plan
from tycho.input.windows import WindowsFocusCheck, WindowsKeySender, foreground_executable
from tycho.ptt.binding import Binding, DeviceKind
from tycho.runtime import Result

ROOT = Path(__file__).resolve().parents[2]
RATE, BLOCK = 16_000, 320
VK_F24 = 0x87
PTT = Binding(DeviceKind.KEYBOARD, VK_F24)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# --- Injecting push-to-talk (F24, by virtual key) ----------------------------------


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32))

    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _U))


def push_to_talk(down: bool) -> None:
    event = INPUT(type=1)
    event.ki = KEYBDINPUT(wVk=VK_F24, dwFlags=0 if down else 0x0002)
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


# --- Recording what reached Windows (low-level keyboard hook) ------------------------


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)


@dataclass
class KeyLog:
    """Injected key events other than push-to-talk, as ("down"|"up", scan, extended)."""

    events: list[tuple[str, int, bool]] = field(default_factory=list[tuple[str, int, bool]])
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_human_key: float = 0.0
    """When a person last typed (a key that wasn't injected), by time.monotonic()."""

    def person_typing(self, within: float = 5.0) -> bool:
        return time.monotonic() - self.last_human_key < within

    def take(self) -> list[tuple[str, int, bool]]:
        with self.lock:
            out, self.events = self.events, []
        return out


class KeyboardHook:
    def __init__(self, log: KeyLog) -> None:
        self._log = log
        self._proc = HOOKPROC(self._on_key)
        self._hook: int | None = None

    def __enter__(self) -> Self:
        self._hook = user32.SetWindowsHookExW(13, self._proc, None, 0)  # WH_KEYBOARD_LL
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())
        return self

    def __exit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        if code == 0:
            info = KBDLLHOOKSTRUCT.from_address(lparam)
            injected = bool(info.flags & 0x10)
            if not injected:
                self._log.last_human_key = time.monotonic()  # only the time, never the key
            elif info.vkCode != VK_F24:
                kind = "up" if wparam in (0x0101, 0x0105) else "down"
                with self._log.lock:
                    self._log.events.append((kind, info.scanCode, bool(info.flags & 0x01)))
        return user32.CallNextHookEx(None, code, wparam, lparam)


# --- The microphone stand-in ---------------------------------------------------------


class Playback:
    """Satisfies ``AudioSource``. Audio is fed in by the test, block by block, in real time."""

    name = "Recordings (self-test)"
    overflows = 0

    def __init__(self) -> None:
        self._buffer = ClipBuffer(RATE, 0.2)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    def feed(self, samples: Samples) -> None:
        for i in range(0, len(samples), BLOCK):
            self._buffer.feed(samples[i : i + BLOCK])
            time.sleep(BLOCK / RATE)

    def start(self) -> None:
        self._buffer.start()

    def quiet_seconds(self) -> float:
        return self._buffer.quiet_seconds()

    def stop(self) -> Samples:
        return self._buffer.stop()


class Speaker:
    """The real feedback sounds, quietly, also noting which played."""

    def __init__(self) -> None:
        self.played: list[str] = []
        self._real = SoundFeedback(SoundDevicePlayer(), volume=0.15)

    def done(self) -> None:
        self.played.append("done")
        self._real.done()

    def not_done(self) -> None:
        self.played.append("not_done")
        self._real.not_done()

    def ask(self) -> None:
        self.played.append("ask")
        self._real.ask()


class SwitchableFocus:
    """The real focus check, pointed at the stand-in window's program."""

    def __init__(self, executable: str) -> None:
        self.real = WindowsFocusCheck(executable)
        self.pretend_elsewhere = False

    def game_has_focus(self) -> bool:
        return not self.pretend_elsewhere and self.real.game_has_focus()


# --- The test ------------------------------------------------------------------------


class SomeoneIsUsingThePC(Exception):
    """Another window took the front, or there was real keyboard or mouse input."""


def read_wav(path: Path) -> Samples:
    return np.asarray(sf.read(path, dtype="float32")[0], dtype=np.float32)


def expected_keys(command_ids: tuple[str, ...], keybinds: Keybinds) -> list[tuple[str, int, bool]]:
    commands = {c.id: c for c in CommandMatcher.default().commands}
    events: list[tuple[str, int, bool]] = []
    for command_id in command_ids:
        action = resolve(commands[command_id], keybinds).action
        if action is None:
            raise ValueError(f"{command_id} has no key, but was reported done")
        press = plan(action, keybinds.modes)
        for _ in range(press.taps):
            events += [("down", k.value, k.extended) for k in press.keys]
            if press.main.device != "wheel":
                events += [("up", k.value, k.extended) for k in reversed(press.keys)]
    return events


def bring_to_front(hwnd: int) -> None:
    foreground = user32.GetForegroundWindow()
    theirs = user32.GetWindowThreadProcessId(foreground, None)
    ours = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(ours, theirs, True)
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(ours, theirs, False)


async def run_tests(
    clips: list[dict[str, object]],
    session_dir: Path,
    window: tk.Tk,
    keylog: KeyLog,
) -> int:
    current = settings.Settings(push_to_talk=str(PTT), feedback_volume=0.15)
    print("Loading keybinds and the speech model (as tycho run does)...")
    store = app.load_keybinds(current, PTT)
    transcriber = app.load_transcriber(current)
    expected: dict[str, list[str]] = tomllib.loads(
        (ROOT / "tools" / "spike" / "expected.toml").read_text("utf-8")
    )
    exe = os.path.basename(sys.executable)
    focus = SwitchableFocus(exe)
    mic, speaker = Playback(), Speaker()
    results: asyncio.Queue[Result] = asyncio.Queue()
    session = app.build_session(
        current,
        PTT,
        store,
        transcriber,
        audio=mic,
        sender=WindowsKeySender(),
        focus=focus,
        feedback=speaker,
        on_result=results.put_nowait,  # called on the loop, so no thread hand-over needed
    )
    # Windows only lets a program take the foreground after it has sent input,
    # so tap F24 once before Tycho is listening.
    push_to_talk(True)
    push_to_talk(False)
    stop = asyncio.Event()
    serving = asyncio.create_task(app.serve(session, store, stop))
    hwnd = int(window.frame(), 16)
    failures: list[str] = []

    async def to_front() -> None:
        for _ in range(30):
            if focus.real.game_has_focus():
                return
            window.after(0, bring_to_front, hwnd)
            await asyncio.sleep(0.1)

    async def speak(audio: Samples) -> None:
        await to_front()
        if keylog.person_typing():
            raise SomeoneIsUsingThePC("someone is typing")
        if not focus.real.game_has_focus():
            raise SomeoneIsUsingThePC(
                f"{foreground_executable() or 'another window'} kept the front"
            )
        await asyncio.to_thread(mic.feed, np.zeros(RATE // 4, dtype=np.float32))
        push_to_talk(True)
        await asyncio.sleep(0.03)
        await asyncio.to_thread(mic.feed, audio)
        push_to_talk(False)

    async def say(audio: Samples) -> Result:
        await speak(audio)
        await asyncio.to_thread(mic.feed, np.zeros(RATE // 4, dtype=np.float32))
        result = await asyncio.wait_for(results.get(), 15)
        await asyncio.sleep(0.2)  # let the hook see the last key up
        return result

    print(f"\nStand-in window: {exe}. Feeding {len(clips)} recordings in real time.\n")
    for clip in clips:
        audio = read_wav(session_dir / str(clip["file"]))
        keylog.take()
        result = await say(audio)
        keys = keylog.take()
        phrase = str(clip["phrase"])
        got = "brain" if result.status == "not_understood" else "+".join(result.commands)
        want = expected.get(phrase)
        problems: list[str] = []
        if result.status == "no_focus":
            problems.append(f"the stand-in wasn't in front ({foreground_executable()} was)")
        if want is not None and got not in want:
            problems.append(f"expected {want}, got {got}")
        if result.status == "done":
            if keys != expected_keys(result.commands, store.keybinds):
                problems.append(f"keys reaching Windows {keys} differ from the plan")
        elif keys:
            problems.append(f"status {result.status} but keys were sent: {keys}")
        mark = "FAIL" if problems else " OK "
        timing = f"{result.total_ms:4.0f} ms"
        print(f"[{mark}] {phrase!r:42} {result.status:14} {timing}  {'; '.join(problems)}")
        if problems:
            failures.append(phrase)

    print("\nExtra checks:")
    first = read_wav(session_dir / str(clips[0]["file"]))
    focus.pretend_elsewhere = True
    keylog.take()
    result = await say(first)
    keys = keylog.take()
    ok = result.status == "no_focus" and not keys
    print(
        f"[{' OK ' if ok else 'FAIL'}] game not in front: {result.status}, {len(keys)} key events"
    )
    if not ok:
        failures.append("no focus")
    focus.pretend_elsewhere = False

    held = next((c for c in clips if c["phrase"] == "max power to weapons"), None)
    if held is not None:  # a 0.6 s hold on F6: interrupt it half way
        audio = read_wav(session_dir / str(held["file"]))
        keylog.take()
        await speak(audio)
        deadline = time.monotonic() + 5
        while not keylog.events and time.monotonic() < deadline:  # noqa: ASYNC110 - set by the hook thread
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.15)  # now part way through the hold
        push_to_talk(True)
        interrupted = await asyncio.wait_for(results.get(), 5)
        push_to_talk(False)
        await asyncio.wait_for(results.get(), 5)  # the too-short second press
        await asyncio.sleep(0.2)
        keys = keylog.take()
        downs = [k for k in keys if k[0] == "down"]
        ups = [k for k in keys if k[0] == "up"]
        still_down = [
            k
            for k in downs
            if user32.GetAsyncKeyState(user32.MapVirtualKeyW(k[1] | (0xE000 if k[2] else 0), 3))
            & 0x8000
        ]
        ok = interrupted.status == "cancelled" and len(downs) == len(ups) and not still_down
        print(
            f"[{' OK ' if ok else 'FAIL'}] interrupting a held key: {interrupted.status}, "
            f"{len(downs)} down / {len(ups)} up, held now: {still_down or 'none'}"
        )
        if not ok:
            failures.append("barge-in during a hold")

    print(
        f"\nFeedback sounds played: {speaker.played.count('done')} done, "
        f"{speaker.played.count('not_done')} not done"
    )
    stop.set()
    await serving
    print("\n" + ("All passed." if not failures else f"{len(failures)} failed: {failures}"))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips", type=int, default=30)
    args = parser.parse_args()
    from tycho import logs

    logs.setup()
    sessions = sorted(
        p for p in (ROOT / "recordings").glob("20*") if (p / "manifest.jsonl").exists()
    )
    if not sessions:
        print("No recordings; make some with tools/spike/record.py")
        return 1
    lines = (sessions[-1] / "manifest.jsonl").read_text("utf-8").splitlines()
    clips = [json.loads(line) for line in lines if line.strip()][: args.clips]

    window = tk.Tk()
    window.title("Tycho self-test: stand-in for Star Citizen")
    window.geometry("520x120")
    tk.Label(window, text="Tycho is pressing keys into this window.\nLeave it in front.").pack(
        expand=True
    )
    keylog = KeyLog()
    outcome: list[int] = []

    def worker() -> None:
        try:
            outcome.append(asyncio.run(run_tests(clips, sessions[-1], window, keylog)))
        except SomeoneIsUsingThePC as e:
            print(f"\nStopped early: {e}. Run it again when nobody is using the PC.")
            outcome.append(3)
        except Exception as e:
            print(f"Self-test crashed: {type(e).__name__}: {e}")
            outcome.append(2)
        finally:
            push_to_talk(False)  # never leave the injected F24 down
            window.after(0, window.destroy)

    with KeyboardHook(keylog):
        threading.Thread(target=worker, daemon=True).start()
        window.mainloop()
    return outcome[0] if outcome else 2


if __name__ == "__main__":
    sys.exit(main())
