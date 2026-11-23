"""What every page of the bridge shares: the window's callbacks, the slow look-ups it
keeps, the undo list, and the plain words for a setting. Out of ``bridge.py`` (#399)."""

import threading
import typing
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tycho import games, updates
from tycho.desktop import setup
from tycho.desktop.commands import Commands
from tycho.desktop.keys import KeyCheck
from tycho.packs import GamePack
from tycho.packs.parts import HangarImport
from tycho.runtime.recent import Exchange
from tycho.settings import Settings

SETUP_CHOICES = frozenset({"game_root", "push_to_talk", "microphone"})
"""Chosen with their own controls (a folder, a button press, a device), from setup."""
UNDO_DEPTH = 20
DEVICES_SECONDS = 10.0
"""How long the list of microphones is kept between redraws."""
KEYBINDS_SECONDS = 30.0
"""How long the keybinds are kept for the Commands page."""
UPDATE_SECONDS = 3600.0
"""How long the window's own check for a newer Tycho is kept: each opening of the
window shouldn't ask GitHub again. The Check for updates button always asks."""
RELEASES_PAGE = f"https://github.com/{updates.REPO}/releases"


type Apply = Callable[[str, object], bool]
"""Applies a change in the running Tycho; False if it can't now (it applies at restart)."""
type Findings = Callable[[], Sequence[Mapping[str, str]]]


class State:
    """The bridge's state. Each page is a class on top of it; ``Bridge`` is all of them."""

    def __init__(
        self,
        *,
        apply: Apply = lambda _name, _value: False,
        findings: Findings = lambda: [],
        settings_path: Any = None,
        key_check: KeyCheck | None = None,
        ship_accent: Callable[[], str | None] = lambda: None,
        pc: setup.SetupPC | None = None,
        choose_folder: Callable[[], str | None] = lambda: None,
        choose_file: Callable[[], str | None] = lambda: None,
        import_hangar: Callable[[GamePack, Path], HangarImport] = games.import_hangar,
        finish: Callable[[], None] = lambda: None,
        first_run: bool = False,
        commands: Commands | None = None,
        latest_release: Callable[[], updates.Release | None] = updates.latest_release,
        open_page: Callable[[str], object] = webbrowser.open,
        restart: Callable[[], bool] = lambda: False,
        quit_tycho: Callable[[], None] | None = None,
        recent: Callable[[], Sequence[Exchange]] = lambda: [],
        listening: Callable[[], bool | None] = lambda: None,
        trying: Callable[[bool], bool] = lambda _on: False,
        opening: str = "",
    ) -> None:
        """``apply`` changes a setting in the running Tycho (the live callbacks);
        ``findings`` are the checks ``tycho check`` makes; ``key_check`` tries a key
        with its provider; ``ship_accent`` is the colour of the ship being flown.
        ``choose_file`` is Windows' own open-file dialog, and ``import_hangar`` reads
        a hangar export into a plan for the fleet (#556; stand-ins in tests).
        ``restart`` restarts the voice loop with the settings as saved (#295); False if
        nothing is running to restart. ``quit_tycho`` stops Tycho, as Quit on the tray
        does (#424); None while there's no running Tycho to stop (setup). ``commands``
        is for tests: otherwise the Commands page is the chosen game's. ``recent`` is
        the last exchanges, from memory (``runtime.recent``); ``listening`` is whether
        the running Tycho is listening (False: paused), None while none is running.
        ``trying`` turns setup's Try it on or off in the running Tycho (ADR 17): False
        if none is running. ``opening`` is where the window opens the first time:
        "try_it" just after a first run's setup, else Home."""
        self._apply = apply
        self._findings = findings
        self._path = settings_path
        self._key_check = key_check
        self._ship_accent = ship_accent
        self._pc = pc or setup.WindowsPC()
        self._cache: dict[str, tuple[object, object]] = {}
        """Slow look-ups (the game, its keybinds, the devices), kept while their inputs
        stay the same: each Next or redraw shouldn't search the disk again."""
        self._choose_folder = choose_folder
        self._choose_file = choose_file
        self._import_hangar = import_hangar
        self._hangar: HangarImport | None = None
        """A hangar import shown to the player, waiting for Save (#556)."""
        self._finish = finish
        self._first_run = first_run
        self._given_commands = commands
        self._restart = restart
        self._quit = quit_tycho
        self._recent = recent
        self._listening = listening
        self._trying = trying
        self._opening = opening
        self._capture = threading.Event()
        """Set to stop a push-to-talk capture the player has left."""
        self._undo: list[tuple[str, object]] = []
        self._latest_release = latest_release
        self._open_page = open_page
        self._checked: tuple[float, updates.Release | None] | None = None
        """When the window last asked GitHub, and what it said: kept for an hour (#376)."""
        self._release: updates.Release | None = None
        """The newer release the last check found: the only page ``open_release`` opens."""

    def _cached(self, what: str, key: object, look: Callable[[], object]) -> object:
        hit = self._cache.get(what)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = look()
        self._cache[what] = (key, value)
        return value


def setting_label(name: str) -> str:
    special = {
        "tile": "Show the Tile",
        "brain_provider": "Who runs the brain",
        "brain_model": "Brain model",
        "brain_seconds": "Time to answer",
        "call_me": "What Tycho calls you",
        "call_me_says": "How to say it",
        "call_me_how_often": "How often",
        "callouts": "Speaking up",
        "confirm_seconds": "Time to say confirm",
        "feedback_volume": "Sound volume",
        "duck_game": "Turn the game down while Tycho speaks",
        "duck_level": "Game volume while Tycho speaks",
        "game": "Game",
        "game_root": "Game folder",
        "push_to_talk": "Push-to-talk",
        "transcription_device": "Speech recognition on",
        "voice": "Voice",
        "voice_model": "Voice model",
    }
    if name in special:
        return special[name]
    return name.removeprefix("tile_").replace("_", " ").capitalize()


def setting_choices(annotation: object) -> tuple[str, ...]:
    options = typing.get_args(getattr(annotation, "__value__", annotation))
    return (
        cast(tuple[str, ...], options)
        if options and all(isinstance(o, str) for o in options)
        else ()
    )


def plain_error(error: str) -> str:
    """Pydantic's message, without its type names and links."""
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith(("1 validation", "For further")) and "errors.pydantic" not in line:
            if "[type=" in line:
                line = line.split("[type=", 1)[0].strip()
            if line and line not in Settings.model_fields:
                return line
    return "that value isn't allowed"
