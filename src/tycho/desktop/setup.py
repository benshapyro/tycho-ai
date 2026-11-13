"""What setup needs from the PC (ADR 17): the game's folders, a button press, the
microphones and their level. A hardware boundary, so a ``Protocol`` (``SetupPC``) with a
fake in ``tests/fakes``; ``WindowsPC`` is the real one.

These run on the bridge's threads, never the event loop, and none of them waits on the
terminal: the window is the only one asking.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import numpy as np

from tycho.packs import GamePack, PackError, built_in
from tycho.settings import Settings

CAPTURE_SECONDS = 15.0
LEVEL_SECONDS = 1.5


@dataclass(frozen=True, slots=True)
class Game:
    found: bool
    channel: str
    folder: str = ""
    problem: str = ""
    needed: bool = True
    """Whether the chosen game needs its folder found. An advisor pack with no install
    part (World of Warcraft: Forever, #300) reads nothing from the game, so setup skips it."""
    name: str = "Star Citizen"
    """The chosen game's name, for setup's first step."""


class SetupPC(Protocol):
    def game(self, current: Settings) -> Game:
        """The game Tycho would use with these settings, or why there's none."""
        ...

    def root(self, folder: str, channel: str) -> str | None:
        """The folder to save as game_root, from whichever level the player picked."""
        ...

    def capture(self, seconds: float, cancel: threading.Event) -> str | None:
        """The next button pressed, as a push_to_talk setting; None if none in time, or
        once ``cancel`` is set."""
        ...

    def name(self, setting: str) -> str:
        """A push_to_talk setting as the player knows it ("Mouse side (forward)")."""
        ...

    def conflicts(self, setting: str, current: Settings) -> list[str]:
        """What the button also does in the game."""
        ...

    def microphones(self) -> list[str]:
        """The microphones connected now, as microphone settings, default first."""
        ...

    def level(self, microphone: str | None) -> dict[str, object]:
        """A short listen: ``ok``, ``name``, ``peak`` and a ``verdict``, or a ``problem``."""
        ...


class WindowsPC:
    """The real PC. Satisfies ``SetupPC``."""

    def __init__(self, *, rescan: bool = False) -> None:
        """``rescan``: look for microphones afresh each time. Only on a first run: while
        Tycho listens, its microphone stream is open, and a rescan would break it."""
        self._rescan = rescan
        self._audio = threading.Lock()
        """PortAudio isn't thread-safe, and each bridge call has a thread of its own: a
        rescan must never run while the level test's stream is open."""

    def game(self, current: Settings) -> Game:
        from tycho.app import find_install

        try:
            install = find_install(current)
        except FileNotFoundError:
            channel = current.game_channel
            return Game(False, channel, problem=f"Star Citizen {channel} wasn't found")
        return Game(True, current.game_channel, str(install.folder))

    def root(self, folder: str, channel: str) -> str | None:
        from tycho.game.install import root_from

        found = root_from(Path(folder), channel)
        return str(found) if found is not None else None

    def capture(self, seconds: float, cancel: threading.Event) -> str | None:
        """Keyboard, mouse (not left click) or joystick. The joystick source is made and
        polled on this thread and closed after, as SDL requires: Tycho's own joystick
        source, on its push-to-talk thread, is untouched."""
        from tycho.app import input_sources
        from tycho.ptt import windows as win
        from tycho.ptt.monitor import capture_binding

        sources = input_sources(None)
        try:
            found = capture_binding(
                sources,
                ignore=frozenset({win.LEFT_MOUSE}),
                timeout=seconds,
                cancelled=cancel.is_set,
            )
        finally:
            for source in sources:
                close: Callable[[], None] | None = getattr(source, "close", None)
                if close is not None:
                    close()
        return str(found) if found is not None else None

    def name(self, setting: str) -> str:
        from tycho.app import describe
        from tycho.ptt.binding import Binding

        return describe(Binding.parse(setting))

    def conflicts(self, setting: str, current: Settings) -> list[str]:
        from tycho.app import conflict_warnings, find_install
        from tycho.game.store import GameDataError, KeybindStore
        from tycho.packs import built_in
        from tycho.ptt.binding import Binding

        if built_in(current.game).parts.keybinds is None:
            return []  # a game Tycho reads no keybinds for: Star Citizen's aren't it (#341)
        try:
            store = KeybindStore.load(find_install(current))
        except FileNotFoundError, GameDataError, OSError:
            return []
        return conflict_warnings(Binding.parse(setting), store)

    def microphones(self) -> list[str]:
        from tycho.audio.microphone import input_devices

        with self._audio:
            return input_devices(rescan=self._rescan)

    def level(self, microphone: str | None) -> dict[str, object]:
        from tycho.app import microphone_device
        from tycho.audio.microphone import Microphone

        try:
            with self._audio, Microphone(device=microphone_device(microphone)) as mic:
                mic.start()
                time.sleep(LEVEL_SECONDS)
                clip = mic.stop()
                name = mic.name
        except Exception as e:
            return {"ok": False, "problem": str(e)}
        return {"ok": True, "name": name, **verdict(clip)}


def verdict(clip: np.ndarray[tuple[int], np.dtype[np.float32]]) -> dict[str, object]:
    """How loud a clip is, by the same rule as ``tycho check`` (``audio.clip.loudness``)."""
    from tycho.audio.clip import loudness

    peak, how = loudness(clip)
    return {"peak": round(peak, 4), "verdict": how}


def game(current: Settings, pc: SetupPC, pack: GamePack | None = None) -> Game:
    """The chosen game as setup sees it: found by ``pc``, or not needed at all when its
    pack reads nothing from the game (no install part, #300, #344). ``pack`` is the
    chosen game's (the settings' when None)."""
    if pack is None:
        try:
            pack = built_in(current.game)
        except PackError:  # tycho check says why; setup asks for Star Citizen as before
            return pc.game(current)
    if pack.parts.install is None:
        return Game(True, current.game_channel, needed=False, name=pack.name)
    return replace(pc.game(current), name=pack.name)


def needs_setup(current: Settings, pc: SetupPC | None = None, pack: GamePack | None = None) -> bool:
    """Whether Tycho can't start without the player: no push-to-talk, or no game folder
    for a game that has one. A pack with no install part (an advisor, #191) needs no
    folder, so Star Citizen missing never sends its player to setup (#344)."""
    if current.push_to_talk is None:
        return True
    return not game(current, pc or WindowsPC(), pack).found
