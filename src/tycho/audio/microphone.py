"""The microphone, via sounddevice (PortAudio). The stream stays open so recording
starts instantly; only audio between ``start`` and ``stop`` is kept."""

# sounddevice ships no type information.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

from types import TracebackType
from typing import Any, Self

import numpy as np
import sounddevice as sd

from tycho.audio.clip import ClipBuffer, Samples

SAMPLE_RATE = 16_000
BLOCK_FRAMES = 320  # 20 ms


class Microphone:
    def __init__(self, device: int | str | None = None, preroll_seconds: float = 0.2) -> None:
        self._buffer = ClipBuffer(SAMPLE_RATE, preroll_seconds)
        self.overflows = 0
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_FRAMES,
            channels=1,
            dtype="float32",
            device=device,
            callback=self._on_audio,
        )

    @property
    def name(self) -> str:
        info: dict[str, Any] = sd.query_devices(self._stream.device)
        return str(info["name"])

    def __enter__(self) -> Self:
        self._stream.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stream.stop()
        self._stream.close()

    def start(self) -> None:
        self._buffer.start()

    def stop(self) -> Samples:
        return self._buffer.stop()

    def _on_audio(self, indata: Any, frames: int, time: Any, status: sd.CallbackFlags) -> None:
        if status.input_overflow:
            self.overflows += 1
        self._buffer.feed(np.array(indata[:, 0], dtype=np.float32))
