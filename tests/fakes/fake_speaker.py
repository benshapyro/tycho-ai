"""A speaker that records what it was asked to say (satisfies ``runtime.Speaker``)."""

import asyncio

from tycho.voice import SpeechError


class FakeSpeaker:
    """Records each line. ``fail`` raises ``SpeechError`` instead of speaking; ``hold``
    keeps every line "playing" until it is cancelled, as a long line would."""

    def __init__(self, *, fail: str | None = None, hold: bool = False) -> None:
        self._fail = fail
        self._hold = hold
        self.spoken: list[str] = []
        self.stopped: list[str] = []
        self.warmed = 0
        self.closed = False
        self.started = asyncio.Event()

    async def speak(self, text: str) -> None:
        if self._fail is not None:
            raise SpeechError(self._fail)
        self.spoken.append(text)
        self.started.set()
        if self._hold:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.stopped.append(text)
                raise

    async def warm(self) -> None:
        self.warmed += 1

    async def close(self) -> None:
        self.closed = True
