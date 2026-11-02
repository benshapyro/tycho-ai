"""A speaker that records what it was asked to say (satisfies ``runtime.Speaker``)."""

import asyncio

from tycho.voice import SpeechError


class FakeSpeaker:
    """Records each line. ``fail`` raises ``SpeechError`` instead of speaking; ``hold``
    keeps every line "playing" until it is cancelled, as a long line would."""

    def __init__(
        self, *, fail: str | None = None, hold: bool = False, seconds: float = 0.0
    ) -> None:
        """``seconds`` is how long each line takes to say."""
        self._fail = fail
        self._hold = hold
        self._seconds = seconds
        self.spoken: list[str] = []
        self.kept: list[str] = []
        """Lines spoken with ``keep``."""
        self.stopped: list[str] = []
        self.warmed = 0
        self.closed = False
        self.started = asyncio.Event()

    async def speak(self, text: str, *, keep: bool = False) -> None:
        if self._fail is not None:
            raise SpeechError(self._fail)
        self.spoken.append(text)
        if keep:
            self.kept.append(text)
        self.started.set()
        try:
            await asyncio.sleep(self._seconds)
        except asyncio.CancelledError:
            self.stopped.append(text)
            raise
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
