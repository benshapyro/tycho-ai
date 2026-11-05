"""The runtime's boundaries: speech recognition, sound, game state (ADR 4: each a Protocol)."""

from typing import Protocol

from tycho.audio.clip import Samples
from tycho.brain import BrainRequest, Decision
from tycho.game.keybinds import Keybinds
from tycho.tile import Card


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

    async def decide(self, request: BrainRequest) -> Decision: ...

    async def warm(self, keybinds: Keybinds) -> None:
        """Get ready for the first request, e.g. by caching the briefing. Never raises."""
        ...


class Speaker(Protocol):
    """Speaks what the brain says (ADR 14). Cancelling ``speak`` stops it at once.

    ``speak`` raises ``tycho.voice.SpeechError`` only if nothing could be spoken,
    so the session can play a sound instead.
    """

    async def speak(self, text: str, *, keep: bool = False) -> None:
        """``keep`` is for lines said again and again (the filler while a search
        runs): the speaker may keep their audio and play it with no wait."""
        ...

    async def warm(self) -> None:
        """Get ready to speak soon, e.g. by opening a connection. Never raises."""
        ...

    async def close(self) -> None: ...


class ActionLog(Protocol):
    """A record of every request that matched a command, for the player to review (ADR 13)."""

    def record(self, entry: dict[str, object]) -> None: ...


class GameState(Protocol):
    """Facts about the player's situation that commands can require, e.g. from Game.log.

    Blocking (it may read a file); the session calls it off the event loop.
    """

    def facts(self) -> frozenset[str]: ...


class Display(Protocol):
    """The Tile, the panel over the game (ADR 16). Every call returns at once."""

    @property
    def on(self) -> bool: ...

    def listening(self) -> None:
        """Push-to-talk is held: the talk light pulses."""
        ...

    def thinking(self) -> None: ...

    def idle(self) -> None: ...

    def show(self, card: Card, *, pinned: bool = False) -> bool:
        """False if the Tile is off or the player hid this kind: say it, don't show it."""
        ...

    def ask(self, question: str, seconds: float) -> None:
        """The safety gate's question, with its countdown."""
        ...

    def answered(self) -> None:
        """The question was answered or replaced: its card goes."""
        ...

    def pin(self) -> bool:
        """ "Keep that up". False if there's nothing to keep."""
        ...

    def again(self) -> bool:
        """ "Show that again": a kept card that gave way. False if there's none."""
        ...

    def clear(self) -> None: ...
