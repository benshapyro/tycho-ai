"""What setup needs from the PC (ADR 17): the game's folders, a button press, the
microphones and their level. Each is a function the bridge is given, so the bridge is
tested with stand-ins and no hardware.

These run on the bridge's threads, never the event loop, and none of them waits on the
terminal: the window is the only one asking.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tycho import settings
from tycho.settings import Settings

log = logging.getLogger(__name__)

CAPTURE_SECONDS = 15.0
LEVEL_SECONDS = 1.5


@dataclass(frozen=True, slots=True)
class Game:
    found: bool
    channel: str
    folder: str = ""
    problem: str = ""


def find_game(current: Settings) -> Game:
    """The game Tycho would use with these settings, or why there's none."""
    from tycho.app import find_install

    try:
        install = find_install(current)
    except FileNotFoundError:
        return Game(
            False, current.game_channel, problem=f"Star Citizen {current.game_channel} wasn't found"
        )
    return Game(True, current.game_channel, str(install.folder))


def game_root(folder: str, channel: str) -> str | None:
    """The folder to save as game_root, from whichever level the player picked."""
    from tycho.game.install import root_from

    root = root_from(Path(folder), channel)
    return str(root) if root is not None else None


def capture_button(seconds: float = CAPTURE_SECONDS) -> str | None:
    """The next button pressed (keyboard, mouse but not left click, or joystick), as a
    push_to_talk setting; None if nothing within ``seconds``."""
    from tycho.app import input_sources
    from tycho.ptt import windows as win
    from tycho.ptt.monitor import capture_binding

    found = capture_binding(
        input_sources(None), ignore=frozenset({win.LEFT_MOUSE}), timeout=seconds
    )
    return str(found) if found is not None else None


def button_name(setting: str) -> str:
    from tycho.app import describe
    from tycho.ptt.binding import Binding

    return describe(Binding.parse(setting))


def button_conflicts(setting: str, current: Settings) -> list[str]:
    """What the button also does in the game, so the player can choose another."""
    from tycho.app import conflict_warnings, find_install
    from tycho.game.store import GameDataError, KeybindStore
    from tycho.ptt.binding import Binding

    try:
        store = KeybindStore.load(find_install(current))
    except FileNotFoundError, GameDataError, OSError:
        return []
    return conflict_warnings(Binding.parse(setting), store)


def microphones() -> list[str]:
    """The input devices Windows offers, by name, default first."""
    from tycho.audio.microphone import input_devices

    return input_devices()


def level(microphone: str | None, seconds: float = LEVEL_SECONDS) -> dict[str, object]:
    """How loud the microphone is right now: its peak, and what that means."""
    from tycho.app import microphone_device
    from tycho.audio.microphone import Microphone

    try:
        with Microphone(device=microphone_device(microphone)) as mic:
            mic.start()
            time.sleep(seconds)
            clip = mic.stop()
            name = mic.name
    except Exception as e:
        return {"ok": False, "problem": str(e)}
    peak = float(np.max(np.abs(clip))) if len(clip) else 0.0
    if peak < 0.003:
        verdict = "silent"
    elif peak < 0.05:
        verdict = "quiet"
    elif peak > 0.98:
        verdict = "clipping"
    else:
        verdict = "good"
    return {"ok": True, "name": name, "peak": round(peak, 4), "verdict": verdict}


def needs_setup(current: Settings, game: Callable[[Settings], Game] = find_game) -> bool:
    """Whether Tycho can't start without the player: no push-to-talk, or no game."""
    return current.push_to_talk is None or not game(current).found


def save(changes: Mapping[str, object], path: Path | None = None) -> Settings:
    return settings.update(changes, path)
