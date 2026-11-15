"""Where speech recognition runs, and what to tell a player whose graphics card could
run it but can't yet. Out of ``app.py`` (#398): ``tycho check`` and starting Tycho share it.
"""

from tycho import paths
from tycho.findings import TYCHO
from tycho.settings import Settings
from tycho.stt import DeviceChoice


def gpu_hint() -> str | None:
    """What to do when an NVIDIA card could run speech recognition but its libraries are
    missing; None if there's no NVIDIA card or it already has them (downloaded, or the
    pip wheels in development), so a card that's merely short of memory gets no hint."""
    import importlib.util

    from tycho.stt import CudaLibraries, nvidia_driver
    from tycho.stt.cuda import DOWNLOAD_BYTES, MIN_DRIVER

    driver = nvidia_driver()
    if driver is None or CudaLibraries(paths.cuda_dir()).ready():
        return None
    if importlib.util.find_spec("nvidia") is not None:
        return None
    if driver < MIN_DRIVER:
        return (
            f"Your NVIDIA card could recognise speech about 10x faster, but needs driver "
            f"{MIN_DRIVER} or newer (you have {driver}). Update it, then run: {GPU_HELP}"
        )
    return (
        "Speech recognition is on the processor. Your NVIDIA card can do it about 10x "
        f"faster with a one-time {DOWNLOAD_BYTES / 1e9:.1f} GB download: {GPU_HELP}"
    )


GPU_HELP = f"{TYCHO} gpu install"


MODEL_HELP = f"{TYCHO} model download"


def device_choice(current: Settings) -> DeviceChoice:
    from tycho.stt import CudaLibraries, choose_device, cuda_available, gpu_free_mb

    if current.transcription_device == "cpu":
        return choose_device("cpu", cuda=False, free_mb=None)
    libraries = CudaLibraries(paths.cuda_dir())
    cuda = cuda_available(libraries.dlls if libraries.ready() else None)
    free_mb = gpu_free_mb() if cuda else None  # no NVIDIA tool runs without CUDA
    return choose_device(current.transcription_device, cuda=cuda, free_mb=free_mb)


def device_name(device: str) -> str:
    return "graphics card" if device == "gpu" else "processor"
