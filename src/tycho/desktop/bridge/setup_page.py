"""Setup: the choices made with their own controls (the game's folder, a button press,
a microphone), saved as they're made; on a first run, finishing starts Tycho."""

import dataclasses
import logging
import threading
import time
from typing import cast

from tycho import settings
from tycho.desktop import setup
from tycho.desktop.bridge.commands_page import CommandsPage
from tycho.desktop.bridge.settings_page import SettingsPage
from tycho.desktop.bridge.state import DEVICES_SECONDS, UNDO_DEPTH, plain_error, setting_choices
from tycho.settings import Settings, SettingsError

log = logging.getLogger(__name__)


class SetupPage(SettingsPage, CommandsPage):
    def setup_state(self) -> dict[str, object]:
        """Everything setup shows: the game, push-to-talk, the microphone, the keys."""
        current = settings.load(self._path)
        game = self._game(current)
        ptt = current.push_to_talk
        return {
            "game": dataclasses.asdict(game),
            "channels": list(setting_choices(Settings.model_fields["game_channel"].annotation)),
            "push_to_talk": {
                "set": ptt is not None,
                "name": self._pc.name(ptt) if ptt else "",
                "conflicts": self._conflicts(ptt, current) if ptt else [],
            },
            "microphone": {"setting": current.microphone or "", "devices": self._devices()},
            "keys": self.keys(),
            "ready": ptt is not None and game.found,
        }

    def set_channel(self, channel: object) -> dict[str, object]:
        """Which version of the game: LIVE, PTU and so on."""
        result, current = self._save_choice("game_channel", channel)
        if current is not None:
            result["game"] = dataclasses.asdict(self._game(current))
        return result

    def choose_game_folder(self) -> dict[str, object]:
        """Pick the game's folder in Windows' own folder dialog."""
        folder = self._choose_folder()
        if not folder:
            return {"ok": False, "cancelled": True}
        channel = settings.load(self._path).game_channel
        root = self._pc.root(folder, channel)
        if root is None:
            return {"ok": False, "problem": f"There's no {channel}\\Data.p4k in or under {folder}."}
        result, current = self._save_choice("game_root", root)
        if current is not None:
            result["game"] = dataclasses.asdict(self._game(current))
        return result

    def capture_push_to_talk(self) -> dict[str, object]:
        """Wait for the player to press the button they want, and save it."""
        self._capture.set()  # one capture at a time: a new one replaces the last
        self._capture = cancel = threading.Event()
        try:
            found = self._pc.capture(setup.CAPTURE_SECONDS, cancel)
        except Exception as e:  # a joystick library that won't start, a device gone
            log.exception("Couldn't listen for a push-to-talk button")
            return {"ok": False, "problem": f"Tycho couldn't listen for a button: {e}"}
        if cancel.is_set():
            return {"ok": False, "cancelled": True}
        if found is None:
            seconds = round(setup.CAPTURE_SECONDS)
            return {"ok": False, "problem": f"No button was pressed in {seconds} seconds."}
        result, current = self._save_choice("push_to_talk", found)
        if current is not None:
            result["name"] = self._pc.name(found)
            result["conflicts"] = self._conflicts(found, current)
        return result

    def cancel_capture(self) -> dict[str, object]:
        """Stop waiting for a push-to-talk button: the player moved on. Otherwise the
        next key they press (Ctrl+V into a key box, Alt+Tab) would be saved."""
        self._capture.set()
        return {"ok": True}

    def set_microphone(self, name: object) -> dict[str, object]:
        """A microphone from the list, empty for Windows' default, or the one already
        saved (which may be a number, or unplugged now)."""
        if not isinstance(name, str):
            return {"ok": False, "problem": "That microphone isn't connected."}
        saved = settings.load(self._path).microphone or ""
        if name and name != saved and name not in self._devices(fresh=True):
            return {"ok": False, "problem": "That microphone isn't connected."}
        result, _ = self._save_choice("microphone", name or None)
        return result

    def microphone_level(self) -> dict[str, object]:
        """A short listen to the chosen microphone."""
        return self._pc.level(settings.load(self._path).microphone)

    def finish_setup(self) -> dict[str, object]:
        """Setup is done: on a first run, Tycho starts."""
        current = settings.load(self._path)
        missing = [
            what
            for what, ok in (
                ("the game", self._game(current).found),
                ("a push-to-talk button", current.push_to_talk is not None),
            )
            if not ok
        ]
        if missing:
            return {"ok": False, "problem": f"Tycho still needs {' and '.join(missing)}."}
        self._capture.set()
        self._finish()
        return {"ok": True, "first_run": self._first_run}

    def _save_choice(self, name: str, value: object) -> tuple[dict[str, object], Settings | None]:
        """A setup choice, saved; it applies when Tycho next starts. Also the settings
        as saved, so the caller needn't read the file again."""
        before = getattr(settings.load(self._path), name)
        try:
            current = settings.update({name: value}, self._path)
        except SettingsError as e:
            return {"ok": False, "problem": plain_error(str(e))}, None
        if getattr(current, name) != before:
            self._undo = [*self._undo, (name, before)][-UNDO_DEPTH:]
        return {"ok": True, "applies": "start" if self._first_run else "restart"}, current

    # Slow look-ups, kept while their inputs are the same.

    def _game(self, current: Settings) -> setup.Game:
        key = (current.game, current.game_channel, str(current.game_root))
        return cast(setup.Game, self._cached("game", key, lambda: setup.game(current, self._pc)))

    def _conflicts(self, ptt: str, current: Settings) -> list[str]:
        key = (ptt, current.game, current.game_channel, str(current.game_root))
        found = self._cached("conflicts", key, lambda: self._pc.conflicts(ptt, current))
        return list(cast(list[str], found))

    def _devices(self, fresh: bool = False) -> list[str]:
        # Microphones come and go, so a new list each time setup is shown, not each click.
        key = time.monotonic() // DEVICES_SECONDS if not fresh else time.monotonic()
        return list(cast(list[str], self._cached("devices", key, self._pc.microphones)))
