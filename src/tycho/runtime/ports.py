"""The runtime's boundaries: speech recognition, sound, game state (ADR 4: each a Protocol)."""

from typing import Protocol

from tycho.audio.clip import Samples


class Transcriber(Protocol):
    """Speech to text. Blocking; the session runs it on its own worker thread."""

    def transcribe(self, samples: Samples, samplerate: int) -> str: ...


class Feedback(Protocol):
    """The sounds that tell the player how a request went. Must return at once, never block."""

    def done(self) -> None: ...

    def not_done(self) -> None: ...


class GameState(Protocol):
    """Facts about the player's situation that commands can require, e.g. from Game.log.

    Blocking (it may read a file); the session calls it off the event loop.
    """

    def facts(self) -> frozenset[str]: ...
