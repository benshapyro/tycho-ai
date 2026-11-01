"""Playing speech as it arrives: 16-bit mono PCM, handed to the sound card in pieces.

The sound card's own thread pulls audio from a buffer that the event loop fills,
so writing never blocks the loop, and stopping is immediate. Playback waits for a
short head start, so one slow piece from the network doesn't leave a gap.
"""

# sounddevice ships no type information.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

import asyncio
import contextlib
import threading
from typing import Any, Protocol

import numpy as np

HEAD_START_SECONDS = 0.1


class Sink(Protocol):
    """Where speech audio goes. ``write`` and ``finish`` return at once."""

    def write(self, pcm: bytes) -> None:
        """More audio: 16-bit signed little-endian mono. Pieces may split a sample."""
        ...

    def finish(self) -> None:
        """No more audio is coming; play what's left."""
        ...

    async def drained(self) -> None:
        """Wait until everything written has played (after ``finish``)."""
        ...

    def close(self) -> None:
        """Stop at once, mid-word if need be. Safe to call more than once."""
        ...


class SoundDeviceSink:
    """Plays on the Windows default output. Satisfies ``Sink``. Open it on the event loop."""

    def __init__(self, rate: int, gain: float = 1.0, head_start: float = HEAD_START_SECONDS):
        import sounddevice as sd

        self._sd = sd
        self._loop = asyncio.get_running_loop()
        self._played = asyncio.Event()
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._carry = b""
        self._finished = False
        self._started = False
        self._closed = False
        self._head_start = round(head_start * rate) * 2
        self._gain = gain
        self._stream = sd.RawOutputStream(
            samplerate=rate,
            channels=1,
            dtype="int16",
            callback=self._fill,
            finished_callback=self._ended,
        )

    def write(self, pcm: bytes) -> None:
        data = self._carry + pcm
        cut = len(data) - len(data) % 2  # keep half a sample for the next piece
        data, self._carry = data[:cut], data[cut:]
        if self._gain != 1.0:
            scaled = np.frombuffer(data, np.int16).astype(np.float32) * self._gain
            data = np.clip(scaled, -32768, 32767).astype(np.int16).tobytes()
        with self._lock:
            self._buffer += data
            ready = len(self._buffer) >= self._head_start
        if ready:
            self._start()

    def finish(self) -> None:
        with self._lock:
            self._finished = True
        self._start()

    async def drained(self) -> None:
        if self._started:
            await self._played.wait()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream.abort(ignore_errors=True)
        self._stream.close(ignore_errors=True)

    def _start(self) -> None:
        if not self._started and not self._closed:
            self._started = True
            self._stream.start()

    def _fill(self, outdata: Any, frames: int, time: Any, status: Any) -> None:
        """On the sound card's thread: the next block, or silence if the network is behind."""
        wanted = frames * 2
        with self._lock:
            chunk = bytes(self._buffer[:wanted])
            del self._buffer[:wanted]
            last = self._finished and not self._buffer
        outdata[: len(chunk)] = chunk
        if len(chunk) < wanted:
            outdata[len(chunk) :] = b"\x00" * (wanted - len(chunk))
        if last:
            raise self._sd.CallbackStop

    def _ended(self) -> None:
        with contextlib.suppress(RuntimeError):  # the loop has already closed
            self._loop.call_soon_threadsafe(self._played.set)
