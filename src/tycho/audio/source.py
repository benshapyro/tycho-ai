"""The audio boundary: anything that captures push-to-talk clips, real or fake."""

from types import TracebackType
from typing import Protocol, Self

from tycho.audio.clip import Samples


class AudioError(Exception):
    """The microphone couldn't be opened or stopped working."""


class AudioSource(Protocol):
    """A microphone that's always listening, keeping only what's between start and stop."""

    @property
    def name(self) -> str: ...

    @property
    def overflows(self) -> int:
        """How many times audio was dropped because it wasn't read in time."""
        ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def start(self) -> None:
        """Start keeping audio, including a short pre-roll from just before."""
        ...

    def quiet_seconds(self) -> float:
        """How long the audio being kept has been quiet at its end."""
        ...

    def stop(self) -> Samples:
        """Stop keeping audio, and return the clip."""
        ...
