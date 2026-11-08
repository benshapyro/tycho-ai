"""Parakeet TDT 0.6B v2 through onnx-asr and ONNX Runtime: the transcriber Tycho ships (ADR 2)."""

import gc
import logging
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

import numpy as np

from tycho.audio.clip import Samples
from tycho.runtime.ports import RecognitionUnavailable
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
RETRY_SECONDS = 30.0
"""After the processor copy fails to load mid-session, the first request this long after
tries again (#210): the failure may be brief, the game holding memory for a moment. Each
failure after doubles the wait, up to ``RETRY_MOST``."""
RETRY_MOST = 300.0


class ParakeetTranscriber:
    """Satisfies ``runtime.Transcriber``. Load once at startup; ``transcribe`` from one thread."""

    def __init__(
        self, model: TextResultsAsrAdapter, device: Device, folder: Path | None = None
    ) -> None:
        self._model: TextResultsAsrAdapter | None = model
        self.device: Device = device
        self._folder = folder
        """Where the model is, to load it on the processor if the graphics card fails."""
        self._lock = threading.Lock()
        self._switching: threading.Thread | None = None
        """The processor copy loading, after the graphics card failed mid-session."""
        self._broken = ""
        """Why the processor copy couldn't load: every request until the retry says so."""
        self._retry_at = 0.0
        self._wait = RETRY_SECONDS

    @classmethod
    def load(cls, folder: Path, device: Device) -> Self:
        """Load the model and warm it up. Falls back to the processor if the graphics card
        won't start it, or fails on the warm-up: free memory is only a guess, and the game
        may take more between the check and the first run (Ben's run, 2026-11-03)."""
        if device == "gpu":
            failure = ""
            try:
                gpu = cls._open(folder, "gpu")
                gpu._warm_up()
                return gpu
            except Exception as e:  # ONNX Runtime raises its own unrelated exception types
                failure = _first_line(e)
            # Out of the except block: the error's traceback, which holds the frames that
            # hold the half-built GPU session, is gone. Drop our own reference too, then
            # collect, so the card's memory is free before the CPU copy loads (#204).
            gpu = None
            gc.collect()
            log.warning(
                "The graphics card couldn't run speech recognition (%s); using the "
                "processor instead",
                failure,
            )
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
        """Raises ``RecognitionUnavailable`` when this request can't be heard: the graphics
        card just failed, or the processor copy is still loading. The player says it
        again; speech that old is never acted on (#204)."""
        if samplerate != SAMPLERATE:
            raise ValueError(f"Parakeet needs {SAMPLERATE} Hz audio, not {samplerate}")
        with self._lock:
            if self._switching is not None:
                raise RecognitionUnavailable(
                    "speech recognition is still switching to the processor"
                )
            if self._broken:
                if time.monotonic() < self._retry_at or self._folder is None:
                    raise RecognitionUnavailable(self._broken)
                retry = self._folder
            else:
                retry = None
            model, device = self._model, self.device
        if retry is not None:
            # Long enough since the processor copy failed to load: try again, for the
            # requests after this one (loading takes about 20 s).
            log.info("Trying again to load speech recognition on the processor")
            self._move_to_processor(retry)
            raise RecognitionUnavailable("speech recognition is still switching to the processor")
        failure, folder = "", self._folder
        try:
            return _recognize(model, samples)
        except Exception as e:
            if device != "gpu" or self._folder is None:
                raise
            failure, folder = _first_line(e), self._folder
        # The game took the card's memory mid-session, or the driver reset. Fail this
        # request now rather than load the CPU copy here (about 20 s) and then act on
        # a command that old. Outside the except block, so the error's traceback, which
        # holds the GPU session, is gone before the CPU copy starts loading.
        log.warning(
            "Speech recognition failed on the graphics card (%s); switching to the "
            "processor, so say that again in a moment",
            failure,
        )
        del model
        self._move_to_processor(folder)
        raise RecognitionUnavailable(
            "speech recognition failed on the graphics card; switching to the processor"
        )

    def _move_to_processor(self, folder: Path) -> None:
        """Load the CPU copy on a thread of its own, for the requests after this one."""
        with self._lock:
            self._model, self.device = None, "cpu"  # the GPU session's last reference
            self._switching = threading.Thread(
                target=self._load_processor, args=(folder,), name="tycho-stt-switch", daemon=True
            )
            self._switching.start()

    def _load_processor(self, folder: Path) -> None:
        gc.collect()  # let go of what the failed session holds on the card
        started = time.perf_counter()
        try:
            cpu = type(self)._open(folder, "cpu")
            cpu._warm_up()
        except Exception as e:
            reason = _first_line(e)
            with self._lock:
                wait = self._wait
                self._broken = f"speech recognition couldn't load on the processor ({reason})"
                self._retry_at = time.monotonic() + wait
                self._wait = min(wait * 2, RETRY_MOST)
                self._switching = None
            log.error(
                "Couldn't load speech recognition on the processor (%s); trying again in "
                "%.0f s, or restart Tycho",
                reason,
                wait,
            )
            return
        with self._lock:
            self._model, self._switching = cpu._model, None
            self._broken, self._wait = "", RETRY_SECONDS
        log.info("Speech recognition is on the processor (%.0f s)", time.perf_counter() - started)

    def _recognize(self, samples: Samples) -> str:
        return _recognize(self._model, samples)


def _recognize(model: TextResultsAsrAdapter | None, samples: Samples) -> str:
    if model is None:
        raise RecognitionUnavailable("speech recognition isn't loaded")
    return str(model.recognize(samples, sample_rate=SAMPLERATE)).strip()


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
