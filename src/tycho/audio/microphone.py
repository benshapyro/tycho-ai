"""The microphone, via sounddevice (PortAudio). The stream stays open so recording
starts instantly; only audio between ``start`` and ``stop`` is kept."""

# sounddevice ships no type information.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

from types import TracebackType
from typing import Any, Self

import numpy as np
import sounddevice as sd

from tycho.audio.clip import ClipBuffer, Samples
from tycho.audio.source import AudioError

SAMPLE_RATE = 16_000
BLOCK_FRAMES = 320  # 20 ms


class Microphone:
    """The real microphone. Satisfies ``AudioSource``."""

    def __init__(self, device: int | str | None = None, preroll_seconds: float = 0.2) -> None:
        self._buffer = ClipBuffer(SAMPLE_RATE, preroll_seconds)
        self.overflows = 0
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                channels=1,
                dtype="float32",
                device=device,
                callback=self._on_audio,
            )
        except (sd.PortAudioError, ValueError) as e:
            which = f'"{device}"' if device is not None else "the default microphone"
            raise AudioError(
                f"Couldn't open {which}: {e}. Check it's plugged in, not muted, and that "
                "Windows allows desktop apps to use the microphone."
            ) from e

    @property
    def name(self) -> str:
        info: dict[str, Any] = sd.query_devices(self._stream.device)
        return str(info["name"])

    def __enter__(self) -> Self:
        try:
            self._stream.start()
        except sd.PortAudioError as e:
            raise AudioError(f"Couldn't start the microphone: {e}") from e
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

    def quiet_seconds(self) -> float:
        return self._buffer.quiet_seconds()

    def stop(self) -> Samples:
        return self._buffer.stop()

    def _on_audio(self, indata: Any, frames: int, time: Any, status: sd.CallbackFlags) -> None:
        if status.input_overflow:
            self.overflows += 1
        self._buffer.feed(np.array(indata[:, 0], dtype=np.float32))
