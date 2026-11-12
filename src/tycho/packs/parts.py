"""The built-in parts a game pack names by id: code that ships in Tycho, never in a pack.

A pack is data (ADR 18). Reading a game's files or calling its services is code, so
it's built into Tycho, reviewed like the rest, and a pack names what it uses in
``pack.toml``'s ``[parts]``. A pack that names an id this registry doesn't have
fails to load.

Each kind of part is a boundary with a fake (ADR 4):

- ``install``: finds the game's folder (``InstallFinder``).
- ``keybinds``: the player's keybinds (``KeybindSource``, which returns ``Keybinds``).
- ``game_state``: facts about the player's situation (``runtime.ports.GameState``).
- ``lookups``: the brain's look-up tools (``brain.lookups.Lookup``).
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from tycho.game.keybinds import Keybinds

type Kind = Literal["install", "keybinds", "game_state", "lookups"]


class InstallFinder(Protocol):
    """Finds the game on this PC."""

    def folder(self) -> Path:
        """The game's folder; raises if it isn't installed."""
        ...


class KeybindSource(Protocol):
    """The keys the player has bound in the game, as Tycho presses them."""

    @property
    def keybinds(self) -> Keybinds: ...


@dataclass(frozen=True, slots=True)
class Registry:
    """The ids of the built-in parts, by kind."""

    install: frozenset[str]
    keybinds: frozenset[str]
    game_state: frozenset[str]
    lookups: frozenset[str]

    def known(self, kind: Kind) -> frozenset[str]:
        match kind:
            case "install":
                return self.install
            case "keybinds":
                return self.keybinds
            case "game_state":
                return self.game_state
            case "lookups":
                return self.lookups

    def with_parts(self, kind: Kind, *ids: str) -> Registry:
        """This registry and more parts: for a test's fake game."""
        match kind:
            case "install":
                return replace(self, install=self.install | set(ids))
            case "keybinds":
                return replace(self, keybinds=self.keybinds | set(ids))
            case "game_state":
                return replace(self, game_state=self.game_state | set(ids))
            case "lookups":
                return replace(self, lookups=self.lookups | set(ids))


BUILT_IN = Registry(
    # game/install.py: the RSI Launcher's library folder, and the player's channel.
    install=frozenset({"star-citizen"}),
    # game/store.py: Data.p4k's defaults with the player's actionmaps.xml on top.
    keybinds=frozenset({"star-citizen"}),
    # game/gamelog.py: Game.log, for what the player is doing now.
    game_state=frozenset({"star-citizen"}),
    # knowledge/ and spoken_settings.py. "settings" is core: every pack gets it.
    lookups=frozenset({"ships", "fleet", "items", "mining", "trade", "settings", "web"}),
)
