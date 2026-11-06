"""What the tray icon's menu offers and does (ADR 17), apart from the icon itself so
it's tested without a taskbar.

Its choices arrive on the tray's thread. Anything that touches the session goes to the
voice loop's own thread (``host.call``); the tooltip is refreshed once that has run, so
it never shows the state from before.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Protocol

from tycho.desktop.tray import SEPARATOR, Item
from tycho.settings import SettingsError

log = logging.getLogger(__name__)


class Pausable(Protocol):
    @property
    def paused(self) -> bool: ...

    def pause(self, paused: bool) -> None: ...


class Calls(Protocol):
    def call(self, fn: Callable[[], None]) -> None: ...

    def ask(self, request: str) -> None: ...


class HasTile(Protocol):
    @property
    def on(self) -> bool: ...


class TrayMenu:
    def __init__(
        self,
        session: Pausable,
        host: Calls,
        *,
        display: HasTile | None = None,
        save: Callable[[Mapping[str, object]], object] = lambda _changes: None,
        apply_tile: Callable[[bool], None] = lambda _on: None,
        open_logs: Callable[[], None] = lambda: None,
    ) -> None:
        """``save`` writes a setting to settings.toml (``settings.update``); ``apply_tile``
        applies the Tile's on/off to the running Tile, as a change by voice does."""
        self._session = session
        self._host = host
        self._display = display
        self._save = save
        self._apply_tile = apply_tile
        self._open_logs = open_logs
        self.refresh: Callable[[], None] = lambda: None
        """Redraws the tray's tooltip; set once the icon exists."""

    def tip(self) -> str:
        return "Tycho: paused" if self._session.paused else "Tycho: listening"

    def items(self) -> list[Item]:
        paused = self._session.paused
        items = [Item("pause", "Resume listening" if paused else "Pause listening")]
        if self._display is not None:
            items.append(Item("tile", "Show the Tile", checked=self._display.on))
        return [*items, SEPARATOR, Item("logs", "Open the logs folder"), Item("quit", "Quit Tycho")]

    def chosen(self, key: str) -> None:
        if key == "pause":
            paused = not self._session.paused

            def apply() -> None:
                self._session.pause(paused)
                self.refresh()

            self._host.call(apply)
            log.info("Listening %s from the tray", "paused" if paused else "resumed")
        elif key == "tile" and self._display is not None:
            on = not self._display.on
            try:
                self._save({"tile": on})
            except SettingsError as e:
                log.warning(
                    "Couldn't save the Tile setting (%s); it changes until Tycho restarts", e
                )
            self._apply_tile(on)
        elif key == "logs":
            self._open_logs()
        elif key == "quit":
            self._host.ask("quit")
