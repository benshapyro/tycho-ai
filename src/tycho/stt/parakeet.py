"""Parakeet TDT 0.6B v2 through onnx-asr and ONNX Runtime: the transcriber Tycho ships (ADR 2)."""

import gc
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

import numpy as np

from tycho.audio.clip import Samples
from tycho.stt.models import PARAKEET_V2, ModelError

if TYPE_CHECKING:
    from onnx_asr.adapters import TextResultsAsrAdapter

log = logging.getLogger(__name__)

SAMPLERATE = 16_000

type Device = Literal["gpu", "cpu"]


class ParakeetTranscriber:
    """Satisfies ``runtime.Transcriber``. Load once at startup; ``transcribe`` from one thread."""

    def __init__(
        self, model: TextResultsAsrAdapter, device: Device, folder: Path | None = None
    ) -> None:
        self._model = model
        self.device: Device = device
        self._folder = folder
        """Where the model is, to load it on the processor if the graphics card fails."""

    @classmethod
    def load(cls, folder: Path, device: Device) -> Self:
        """Load the model and warm it up. Falls back to the processor if the graphics card
        won't start it, or fails on the warm-up: free memory is only a guess, and the game
        may take more between the check and the first run (Ben's run, 2026-11-03)."""
        if device == "gpu":
            try:
                transcriber = cls._open(folder, "gpu")
                transcriber._warm_up()
            except Exception as e:  # ONNX Runtime raises its own unrelated exception types
                log.warning(
                    "The graphics card couldn't run speech recognition (%s); using the "
                    "processor instead",
                    _first_line(e),
                )
                gc.collect()  # let go of what the failed session holds on the card
            else:
                return transcriber
        try:
            transcriber = cls._open(folder, "cpu")
            transcriber._warm_up()
        except Exception as e:
            raise ModelError(f"Couldn't load the speech model from {folder}: {e}") from e
        return transcriber

    def _warm_up(self) -> None:
        # The first call compiles kernels and allocates memory; pay for it now, not on a command.
        self._recognize(np.zeros(SAMPLERATE, dtype=np.float32))

    @classmethod
    def _open(cls, folder: Path, device: Device) -> Self:
        import onnx_asr  # imports ONNX Runtime: slow, so only when actually loading
        import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]

        ort.set_default_logger_severity(3)  # pyright: ignore[reportUnknownMemberType] - errors only
        providers = ["CPUExecutionProvider"]
        if device == "gpu":
            providers.insert(0, "CUDAExecutionProvider")
        model = onnx_asr.load_model(  # pyright: ignore[reportUnknownMemberType] - ORT's types
            PARAKEET_V2.name, folder, providers=providers
        )
        return cls(model, device_in_use(model, device), folder)

    def transcribe(self, samples: Samples, samplerate: int) -> str:
        if samplerate != SAMPLERATE:
            raise ValueError(f"Parakeet needs {SAMPLERATE} Hz audio, not {samplerate}")
        try:
            return self._recognize(samples)
        except Exception as e:
            if self.device != "gpu" or self._folder is None:
                raise
            # The game took the card's memory mid-session, or the driver reset: carry on
            # on the processor for the rest of the session, and do this request there.
            log.warning(
                "Speech recognition failed on the graphics card (%s); using the processor "
                "from now on",
                _first_line(e),
            )
            del self._model
            gc.collect()
            cpu = type(self)._open(self._folder, "cpu")
            self._model, self.device = cpu._model, "cpu"
            return self._recognize(samples)

    def _recognize(self, samples: Samples) -> str:
        return str(self._model.recognize(samples, sample_rate=SAMPLERATE)).strip()


def _first_line(error: Exception) -> str:
    return (str(error).strip().splitlines() or [type(error).__name__])[0]


def device_in_use(model: Any, asked: Device) -> Device:
    """Where the model really runs. ONNX Runtime falls back to the CPU without an
    error when the CUDA libraries won't load, so "gpu" is only believed if the
    session's first provider is CUDA. Reads an onnx-asr internal (pinned version);
    if that changes, the request is taken at its word.
    """
    session = getattr(getattr(model, "asr", None), "_encoder", None)
    get_providers = getattr(session, "get_providers", None)
    if asked != "gpu" or get_providers is None:
        return asked
    providers: list[str] = list(get_providers())
    if providers and providers[0] != "CUDAExecutionProvider":
        log.warning(
            "The graphics card's libraries didn't load, so speech recognition is on the CPU"
        )
        return "cpu"
    return asked
