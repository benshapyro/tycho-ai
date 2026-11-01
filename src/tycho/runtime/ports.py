"""The runtime's boundaries: speech recognition, sound, game state (ADR 4: each a Protocol)."""

from collections.abc import Sequence
from typing import Protocol

from tycho.audio.clip import Samples
from tycho.brain import Decision, Exchange
from tycho.game.keybinds import Keybinds


class Transcriber(Protocol):
    """Speech to text. Blocking; the session runs it on its own worker thread."""

    def transcribe(self, samples: Samples, samplerate: int) -> str: ...


class Feedback(Protocol):
    """The sounds that tell the player how a request went. Must return at once, never block."""

    def done(self) -> None: ...

    def not_done(self) -> None: ...

    def ask(self) -> None:
        """Tycho wants the player to say "confirm" before it acts."""
        ...


class Brain(Protocol):
    """Decides what to do with a request the fast path couldn't match (ADR 14).

    Raises ``tycho.brain.BrainError`` when it has no usable answer. Its decision
    is untrusted: the session turns it into commands and the safety gate checks them.
    """

    async def decide(
        self, text: str, recent: Sequence[Exchange], keybinds: Keybinds
    ) -> Decision: ...

    async def warm(self, keybinds: Keybinds) -> None:
        """Get ready for the first request, e.g. by caching the briefing. Never raises."""
        ...


class ActionLog(Protocol):
    """A record of every request that matched a command, for the player to review (ADR 13)."""

    def record(self, entry: dict[str, object]) -> None: ...


class GameState(Protocol):
    """Facts about the player's situation that commands can require, e.g. from Game.log.

    Blocking (it may read a file); the session calls it off the event loop.
    """

    def facts(self) -> frozenset[str]: ...
