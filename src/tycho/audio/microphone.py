"""The microphone, via sounddevice (PortAudio). The stream stays open so recording
starts instantly; only audio between ``start`` and ``stop`` is kept.

If the microphone goes away (a USB headset unplugged, Windows switching the
default device), PortAudio just stops calling back. So each press checks the
stream is still delivering sound and, if not, reopens it once; if that fails, or
it dies mid-press, it raises ``AudioError`` so the player hears and reads why.
"""

# sounddevice ships no type information.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false

import contextlib
import logging
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

import numpy as np
import sounddevice as sd

from tycho.audio.clip import ClipBuffer, Samples
from tycho.audio.source import AudioError

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BLOCK_FRAMES = 320  # 20 ms
SILENT_STREAM_SECONDS = 1.0
"""With no audio for this long (50 blocks), the microphone has gone away."""


def _rescan() -> None:
    """Make PortAudio look for devices again, so a re-plugged headset is found."""
    sd._terminate()  # pyright: ignore[reportPrivateUsage]
    sd._initialize()  # pyright: ignore[reportPrivateUsage]


class Microphone:
    """The real microphone. Satisfies ``AudioSource``."""

    def __init__(
        self,
        device: int | str | None = None,
        preroll_seconds: float = 0.2,
        *,
        clock: Callable[[], float] = time.monotonic,
        rescan: Callable[[], None] = _rescan,
    ) -> None:
        self._device = device
        self._buffer = ClipBuffer(SAMPLE_RATE, preroll_seconds)
        self._clock = clock
        self._rescan = rescan
        self.overflows = 0
        self._heard_at = clock()
        self._stream = self._open()

    def _open(self) -> Any:
        try:
            return sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                channels=1,
                dtype="float32",
                device=self._device,
                callback=self._on_audio,
            )
        except (sd.PortAudioError, ValueError) as e:
            raise AudioError(
                f"Couldn't open {self._which}: {e}. Check it's plugged in, not muted, and that "
                "Windows allows desktop apps to use the microphone."
            ) from e

    @property
    def _which(self) -> str:
        return f'"{self._device}"' if self._device is not None else "the default microphone"

    @property
    def name(self) -> str:
        info: dict[str, Any] = sd.query_devices(self._stream.device)
        return str(info["name"])

    def __enter__(self) -> Self:
        self._start_stream()
        return self

    def _start_stream(self) -> None:
        try:
            self._stream.start()
        except sd.PortAudioError as e:
            raise AudioError(f"Couldn't start the microphone: {e}") from e
        self._heard_at = self._clock()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stream.stop()
        self._stream.close()

    def start(self) -> None:
        if not self._delivering():
            self._reopen()
        self._buffer.start()

    def quiet_seconds(self) -> float:
        return self._buffer.quiet_seconds()

    def stop(self) -> Samples:
        clip = self._buffer.stop()
        if not self._delivering():
            raise AudioError(f"{self._which} stopped sending sound. Is it still plugged in?")
        return clip

    def _delivering(self) -> bool:
        return bool(self._stream.active) and (
            self._clock() - self._heard_at < SILENT_STREAM_SECONDS
        )

    def _reopen(self) -> None:
        """Open the microphone again after it went away; ``AudioError`` if it can't be."""
        log.warning("%s stopped sending sound; opening it again", self._which)
        with contextlib.suppress(sd.PortAudioError):
            self._stream.close()
        try:
            self._rescan()
        except sd.PortAudioError as e:
            raise AudioError(f"{self._which} went away and couldn't be found again: {e}") from e
        self._stream = self._open()
        self._start_stream()
        log.info("%s is working again", self._which)

    def _on_audio(self, indata: Any, frames: int, time: Any, status: sd.CallbackFlags) -> None:
        self._heard_at = self._clock()
        if status.input_overflow:
            self.overflows += 1
        self._buffer.feed(np.array(indata[:, 0], dtype=np.float32))


def input_devices() -> list[str]:
    """The microphones Windows offers, default first, each as a microphone setting:
    "name, host API" ("Desk mic, MME"). A bare name can match the same microphone under
    several host APIs, and sounddevice then refuses it; this exact string matches one.
    Only the default host API's devices (each microphone is listed once per host API),
    and none of Windows' stand-ins ("Microsoft Sound Mapper"). Empty if PortAudio can't
    list them."""
    try:
        devices = sd.query_devices()
        default = sd.default.device[0]
        api = sd.query_hostapis(sd.default.hostapi) if sd.default.hostapi >= 0 else None
    except Exception:
        log.debug("Couldn't list microphones", exc_info=True)
        return []
    ours = set(api["devices"]) if api is not None else set(range(len(devices)))
    api_name = str(api["name"]) if api is not None else ""
    names: list[str] = []
    for index, device in enumerate(devices):
        name = str(device.get("name", ""))
        if index not in ours or device.get("max_input_channels", 0) <= 0:
            continue
        if not name or name.startswith(_STAND_INS):
            continue
        setting = f"{name}, {api_name}" if api_name else name
        if setting in names:
            continue
        if index == default:
            names.insert(0, setting)
        else:
            names.append(setting)
    return names


_STAND_INS = ("Microsoft Sound Mapper", "Primary Sound Capture Driver")
