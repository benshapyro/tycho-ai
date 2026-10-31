"""Choosing where speech recognition runs: the graphics card if it has room, else the processor.

The GPU session reserved about 3.5 GB of graphics memory in the benchmark, with
Star Citizen already using about 8 GB. Revisited after the frame-rate test (#23).
"""

import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

GPU_MEMORY_NEEDED_MB = 4_500
"""The 3.5 GB the model reserved, plus room so the game isn't squeezed."""

type Setting = Literal["auto", "gpu", "cpu"]


@dataclass(frozen=True, slots=True)
class DeviceChoice:
    device: Literal["gpu", "cpu"]
    reason: str


def choose_device(setting: Setting, *, cuda: bool, free_mb: int | None) -> DeviceChoice:
    """Decide from the setting, whether CUDA works, and the graphics card's free memory."""
    if setting == "cpu":
        return DeviceChoice("cpu", "set to the processor in settings")
    if not cuda:
        why = "CUDA isn't available"
        if setting == "gpu":
            return DeviceChoice("cpu", f"set to the graphics card, but {why}; using the processor")
        return DeviceChoice("cpu", f"{why}, so using the processor")
    if setting == "gpu":
        return DeviceChoice("gpu", "set to the graphics card in settings")
    if free_mb is None:
        return DeviceChoice("cpu", "couldn't read the graphics card's free memory")
    if free_mb < GPU_MEMORY_NEEDED_MB:
        return DeviceChoice(
            "cpu",
            f"the graphics card has {free_mb / 1024:.1f} GB free and needs "
            f"{GPU_MEMORY_NEEDED_MB / 1024:.1f} GB",
        )
    return DeviceChoice("gpu", f"the graphics card has {free_mb / 1024:.1f} GB free")


def gpu_free_mb() -> int | None:
    """Free memory on the first NVIDIA card, or None if there isn't one or it can't be read."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - a fixed command, found on PATH
            [smi, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return int(out.stdout.split()[0])
    except OSError, subprocess.TimeoutExpired, IndexError, ValueError:
        return None


def cuda_available() -> bool:
    """Whether ONNX Runtime can use CUDA, loading the CUDA libraries from their pip wheels."""
    # Slow to import, so only when needed. ONNX Runtime ships no type information.
    import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]

    try:
        ort.preload_dlls()  # pyright: ignore[reportUnknownMemberType]
    except OSError, RuntimeError:
        return False
    providers: list[str] = ort.get_available_providers()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return "CUDAExecutionProvider" in providers
