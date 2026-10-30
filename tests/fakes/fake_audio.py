"""A microphone that plays back prepared audio, for tests."""

from types import TracebackType
from typing import Self

import numpy as np

from tycho.audio.clip import ClipBuffer, Samples


class FakeAudioSource:
    """Satisfies ``AudioSource``. Call ``hear`` to feed audio as if it arrived from a microphone."""

    name = "Fake microphone"

    def __init__(self, samplerate: int = 16_000, preroll_seconds: float = 0.2) -> None:
        self._buffer = ClipBuffer(samplerate, preroll_seconds)
        self.overflows = 0
        self.open = False

    def __enter__(self) -> Self:
        self.open = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.open = False

    def hear(self, samples: Samples, block: int = 320) -> None:
        for i in range(0, len(samples), block):
            self._buffer.feed(np.asarray(samples[i : i + block], dtype=np.float32))

    def start(self) -> None:
        self._buffer.start()

    def stop(self) -> Samples:
        return self._buffer.stop()
