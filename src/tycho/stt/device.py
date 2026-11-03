"""Choosing where speech recognition runs: the graphics card if it has room, else the processor.

The GPU session reserved about 3.5 GB of graphics memory in the benchmark, with
Star Citizen already using about 8 GB. Revisited after the frame-rate test (#23).
"""

import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
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
    value = _smi("memory.free")
    return int(value) if value is not None and value.isdigit() else None


def nvidia_driver() -> int | None:
    """The NVIDIA driver's major version (e.g. 581), or None without an NVIDIA card."""
    value = _smi("driver_version")
    try:
        return int(value.split(".")[0]) if value else None
    except ValueError:
        return None


def _smi(field: str) -> str | None:
    """One field for the first NVIDIA card, from nvidia-smi."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - a fixed command, found on PATH
            [smi, f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.split()[0]
    except OSError, subprocess.TimeoutExpired, IndexError:
        return None


def cuda_available(libraries: Path | None = None) -> bool:
    """Whether ONNX Runtime can use CUDA. Its libraries come from ``libraries`` (what
    ``tycho gpu install`` downloaded) when given, else from pip wheels in development.

    ONNX Runtime's GPU build lists CUDA as available whether or not NVIDIA's libraries
    are there, and only prints when they fail to load; so with neither a folder nor the
    wheels, the answer is no (the installed app without `tycho gpu install`)."""
    if libraries is None and importlib.util.find_spec("nvidia") is None:
        return False
    # Slow to import, so only when needed. ONNX Runtime ships no type information.
    import onnxruntime as ort  # pyright: ignore[reportMissingTypeStubs]

    try:
        if libraries is not None:
            ort.preload_dlls(directory=str(libraries))  # pyright: ignore[reportUnknownMemberType]
        else:
            ort.preload_dlls()  # pyright: ignore[reportUnknownMemberType]
    except OSError, RuntimeError:
        return False
    providers: list[str] = ort.get_available_providers()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return "CUDAExecutionProvider" in providers
