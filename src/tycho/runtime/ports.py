"""The runtime's boundaries to speech recognition and sound (ADR 4: every service is a Protocol)."""

from typing import Protocol

from tycho.audio.clip import Samples


class Transcriber(Protocol):
    """Speech to text. Blocking; the session runs it on its own worker thread."""

    def transcribe(self, samples: Samples, samplerate: int) -> str: ...


class Feedback(Protocol):
    """The sounds that tell the player how a request went. Must return at once, never block."""

    def done(self) -> None: ...

    def not_done(self) -> None: ...
