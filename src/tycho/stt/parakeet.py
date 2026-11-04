"""Parakeet TDT 0.6B v2 through onnx-asr and ONNX Runtime: the transcriber Tycho ships (ADR 2)."""

import gc
import logging
from collections.abc import Mapping
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

LEAN_CUDA: Mapping[str, str] = {
    # Grow the memory pool by what's asked for, not by doubling: ONNX Runtime's default
    # keeps up to twice what the model needs, and Star Citizen wants the rest.
    "arena_extend_strategy": "kSameAsRequested",
    # Pick convolution algorithms by rule, without trying each (which allocates the
    # biggest workspace any of them needs), and keep cuDNN's workspace small.
    "cudnn_conv_algo_search": "HEURISTIC",
    "cudnn_conv_use_max_workspace": "0",
}
"""CUDA settings that keep the speech model's graphics memory small, so it can share the
card with the game (Ben's run with Star Citizen open, 2026-11-03)."""
SMALL_PARTS_ON_CPU = False
"""Run the preprocessor and the small decoder on the processor, and only the encoder on
the graphics card: the decoder runs about 30 times a phrase, and each tiny GPU call waits
behind the game's frames (#58). Off until measured with the game running."""
CUDA_OPTIONS: Mapping[str, str] = LEAN_CUDA
"""What Tycho uses: see tools/bench/gpu_memory.py for the measurements behind it."""


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
    def _open(
        cls,
        folder: Path,
        device: Device,
        cuda_options: Mapping[str, str] | None = None,
        small_parts_on_cpu: bool | None = None,
    ) -> Self:
        import onnx_asr  # imports ONNX Runtime: slow, so only when actually loading
        import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]

        ort.set_default_logger_severity(3)  # pyright: ignore[reportUnknownMemberType] - errors only
        providers: list[str | tuple[str, dict[str, str]]] = ["CPUExecutionProvider"]
        split = SMALL_PARTS_ON_CPU if small_parts_on_cpu is None else small_parts_on_cpu
        extra: dict[str, Any] = {}
        if device == "gpu":
            options = CUDA_OPTIONS if cuda_options is None else cuda_options
            providers.insert(0, ("CUDAExecutionProvider", dict(options)))
            if split:  # the features, computed in NumPy on the processor
                extra["preprocessor_config"] = {"use_numpy_preprocessors": True}
        model = onnx_asr.load_model(  # pyright: ignore[reportUnknownMemberType] - ORT's types
            PARAKEET_V2.name, folder, providers=providers, **extra
        )
        if device == "gpu" and split:
            _decoder_on_cpu(model, folder)
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


DECODER_FILE = "decoder_joint-model.onnx"


def _decoder_on_cpu(model: Any, folder: Path) -> None:
    """Swap the model's decoder for one on the processor. Reads an onnx-asr internal
    (pinned version), like ``device_in_use``; if it has changed, the model is left as is."""
    import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]

    asr = getattr(model, "asr", None)
    if asr is None or not hasattr(asr, "_decoder_joint"):
        log.warning("Couldn't move the speech decoder to the processor; it stays where it is")
        return
    asr._decoder_joint = ort.InferenceSession(  # pyright: ignore[reportUnknownMemberType]
        str(folder / DECODER_FILE), providers=["CPUExecutionProvider"]
    )


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
