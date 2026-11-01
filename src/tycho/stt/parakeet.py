"""Parakeet TDT 0.6B v2 through onnx-asr and ONNX Runtime: the transcriber Tycho ships (ADR 2)."""

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

    def __init__(self, model: TextResultsAsrAdapter, device: Device) -> None:
        self._model = model
        self.device: Device = device

    @classmethod
    def load(cls, folder: Path, device: Device) -> Self:
        """Load the model and warm it up. Falls back to the processor if the GPU won't start."""
        try:
            transcriber = cls._open(folder, device)
        except Exception as e:  # ONNX Runtime raises its own unrelated exception types
            if device == "cpu":
                raise ModelError(f"Couldn't load the speech model from {folder}: {e}") from e
            log.warning("The graphics card couldn't run speech recognition (%s); using the CPU", e)
            return cls.load(folder, "cpu")
        # The first call compiles kernels and allocates memory; pay for it now, not on a command.
        transcriber.transcribe(np.zeros(SAMPLERATE, dtype=np.float32), SAMPLERATE)
        return transcriber

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
        return cls(model, device_in_use(model, device))

    def transcribe(self, samples: Samples, samplerate: int) -> str:
        if samplerate != SAMPLERATE:
            raise ValueError(f"Parakeet needs {SAMPLERATE} Hz audio, not {samplerate}")
        return str(self._model.recognize(samples, sample_rate=SAMPLERATE)).strip()


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
