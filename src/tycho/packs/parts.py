"""The built-in parts a game pack names by id: code that ships in Tycho, never in a pack.

A pack is data (ADR 18). Reading a game's files or calling its services is code, so
it's built into Tycho, reviewed like the rest, and a pack names what it uses in
``pack.toml``'s ``[parts]``. A pack that names an id this registry doesn't have
fails to load. The registry itself is ``tycho.games.BUILT_IN``, which lists every
built-in part once (ADR 19).

Each kind of part is a boundary with a fake (ADR 4):

- ``install``: finds the game's folder (``InstallFinder``).
- ``keybinds``: the player's keybinds (``KeybindSource``, which returns ``Keybinds``).
- ``game_state``: facts about the player's situation (``runtime.ports.GameState``),
  built from the game's folder.
- ``lookups``: the brain's look-up tools (``brain.lookups.Lookup``), built in a
  ``Workshop``.

A game-state or look-up id maps to the function that builds it. Builders import their
code when called, so start-up stays as fast.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from tycho.keybinds import Keybinds

if TYPE_CHECKING:
    from tycho.brain.lookups import Lookup
    from tycho.packs.pack import GamePack
    from tycho.runtime.ports import GameState
    from tycho.settings import Settings
    from tycho.spoken_settings import Live

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


@dataclass(slots=True)
class Workshop:
    """What a look-up's builder may use, for one brain. Builders share parts through
    ``shared`` (Star Citizen's ships and fleet read one ship library)."""

    pack: GamePack
    current: Settings
    live: Live | None = None
    brain_provider: str = "anthropic"
    search_key: Callable[[], str | None] = lambda: None
    """The Anthropic key web search runs on, if the player has one (read when asked)."""
    _made: dict[str, object] = field(default_factory=dict[str, object])

    def shared[T](self, name: str, make: Callable[[], T]) -> T:
        """The part ``name``, made once for this brain."""
        if name not in self._made:
            self._made[name] = make()
        return self._made[name]  # pyright: ignore[reportReturnType]


@dataclass(frozen=True, slots=True)
class Built:
    """What one look-up id adds to the brain."""

    lookups: tuple[Lookup, ...] = ()
    about: Callable[[], str] | None = None
    """A line about the player for the brain's request (the fleet's ship being flown)."""
    accent: Callable[[], str | None] | None = None
    """The Tile's colour for what the player is doing (the ship being flown)."""


type GameStateBuilder = Callable[[Path], GameState]
"""Builds a game-state part from the game's folder."""
type LookupBuilder = Callable[[Workshop], Built]


def _not_built(*_: object) -> Built:
    raise NotImplementedError("a test's fake part: Tycho has no code for it")


@dataclass(frozen=True, slots=True)
class Registry:
    """The built-in parts, by kind: ids, and for game state and look-ups their builders."""

    install: frozenset[str]
    keybinds: frozenset[str]
    game_state: Mapping[str, GameStateBuilder]
    lookups: Mapping[str, LookupBuilder]

    def known(self, kind: Kind) -> frozenset[str]:
        match kind:
            case "install":
                return self.install
            case "keybinds":
                return self.keybinds
            case "game_state":
                return frozenset(self.game_state)
            case "lookups":
                return frozenset(self.lookups)

    def with_parts(self, kind: Kind, *ids: str) -> Registry:
        """This registry and more parts, which build nothing: for a test's fake game."""
        match kind:
            case "install":
                return replace(self, install=self.install | set(ids))
            case "keybinds":
                return replace(self, keybinds=self.keybinds | set(ids))
            case "game_state":
                built = dict.fromkeys(ids, _not_built)
                return replace(self, game_state={**self.game_state, **built})
            case "lookups":
                built = dict.fromkeys(ids, _not_built)
                return replace(self, lookups={**self.lookups, **built})
