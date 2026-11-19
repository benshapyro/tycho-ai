"""Choosing where speech recognition runs: the processor unless the player picks the
graphics card.

Tycho runs while a game plays, and the game wants the graphics card. The full model took
3.0 GB of its memory with Tycho's lean CUDA settings (tools/bench/gpu_memory.py,
2026-11-04), and the game frees and takes memory as it plays. On 2026-11-15 the
maintainer's PC had GPU hangs in two games, and one full freeze, with Tycho's model on
the same card. The 8-bit copy on the processor is about 0.4 s a phrase and as accurate
(#188), so "auto" uses it and leaves the card to the game. "gpu" is still there for a
player who wants the speed and has the memory.
"""

import importlib.util
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type Setting = Literal["auto", "gpu", "cpu"]


@dataclass(frozen=True, slots=True)
class DeviceChoice:
    device: Literal["gpu", "cpu"]
    reason: str


def choose_device(setting: Setting, *, cuda: bool) -> DeviceChoice:
    """Decide from the setting and whether CUDA works."""
    if setting == "cpu":
        return DeviceChoice("cpu", "set to the processor in settings")
    if setting == "auto":
        return DeviceChoice("cpu", "the game keeps the graphics card to itself")
    if not cuda:
        return DeviceChoice(
            "cpu", "set to the graphics card, but CUDA isn't available; using the processor"
        )
    return DeviceChoice("gpu", "set to the graphics card in settings")


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


def smi_path(environ: Mapping[str, str] = os.environ) -> Path | None:
    """Where nvidia-smi is, never looking in the current folder.

    ``shutil.which`` on Windows tries the current folder first, so an
    ``nvidia-smi.exe`` saved to Downloads would run whenever Tycho was started from
    there (audit, 2026-11-03). The driver's own places come first, then PATH's
    absolute folders.
    """
    places: list[Path] = []
    if root := environ.get("SystemRoot"):
        places.append(Path(root) / "System32")
    if programs := environ.get("ProgramFiles"):
        places.append(Path(programs) / "NVIDIA Corporation" / "NVSMI")
    places += [Path(p) for p in environ.get("PATH", "").split(os.pathsep) if Path(p).is_absolute()]
    for folder in places:
        candidate = folder / "nvidia-smi.exe"
        if candidate.is_file():
            return candidate
    return None


def _smi(field: str) -> str | None:
    """One field for the first NVIDIA card, from nvidia-smi."""
    smi = smi_path()
    if smi is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - a fixed command, from a known folder
            [smi, f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            # The Start menu's Tycho has no console: without this, each check flashes a
            # console window, which can take focus from the game (review of #157).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
