"""NVIDIA's CUDA libraries, downloaded on request (#56): against tiny fake wheels,
so no real download is needed."""

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tycho import app
from tycho.stt import ModelError, cuda
from tycho.stt.cuda import CudaLibraries, Wheel


def _wheel(name: str, files: dict[str, bytes]) -> tuple[Wheel, bytes]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    data = buffer.getvalue()
    return Wheel(
        name, f"https://example.com/{name}.whl", len(data), hashlib.sha256(data).hexdigest()
    ), data


CUBLAS = _wheel(
    "cublas",
    {
        "nvidia/cublas/bin/cublas64_13.dll": b"cublas",
        "nvidia/cublas/include/cublas.h": b"header",  # not a DLL: left out
        "nvidia_cublas-13.dist-info/RECORD": b"record",
    },
)
CUDNN = _wheel("cudnn", {"nvidia/cudnn/bin/cudnn64_9.dll": b"cudnn", "../../escape.dll": b"x"})


class FakeDownloader:
    def __init__(self, serve: dict[str, bytes], fail: bool = False) -> None:
        self.serve = serve
        self.fail = fail
        self.fetched: list[str] = []

    def fetch(self, url: str, dest: Path, progress: Callable[[int, int], None]) -> None:
        self.fetched.append(url)
        if self.fail:
            raise OSError("connection reset")
        data = self.serve[url]
        dest.write_bytes(data)
        progress(len(data), len(data))


@pytest.fixture
def wheels(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    monkeypatch.setattr(cuda, "WHEELS", (CUBLAS[0], CUDNN[0]))
    return {CUBLAS[0].url: CUBLAS[1], CUDNN[0].url: CUDNN[1]}


def test_only_the_dlls_are_kept_in_one_folder(tmp_path: Path, wheels: dict[str, bytes]) -> None:
    libraries = CudaLibraries(tmp_path / "cuda", FakeDownloader(wheels))
    assert not libraries.ready()
    dlls = libraries.ensure()
    assert sorted(p.name for p in dlls.iterdir()) == [
        "cublas64_13.dll",
        "cudnn64_9.dll",
        "escape.dll",
    ]
    assert not (tmp_path / "escape.dll").exists()  # a path in a wheel never leaves the folder
    assert not list((tmp_path / "cuda").glob("*.whl*"))  # the wheels themselves are deleted
    assert libraries.ready()


def test_a_finished_wheel_isnt_fetched_again(tmp_path: Path, wheels: dict[str, bytes]) -> None:
    first = FakeDownloader(wheels)
    CudaLibraries(tmp_path, first).ensure()
    again = FakeDownloader(wheels, fail=True)  # offline: nothing to fetch
    CudaLibraries(tmp_path, again).ensure()
    assert again.fetched == []


def test_a_wrong_download_is_deleted_and_reported(tmp_path: Path, wheels: dict[str, bytes]) -> None:
    wheels[CUDNN[0].url] = b"not the wheel"
    libraries = CudaLibraries(tmp_path, FakeDownloader(wheels))
    with pytest.raises(ModelError, match="checksum"):
        libraries.ensure()
    assert not libraries.ready()
    assert not list(tmp_path.glob("*.part"))
    assert (tmp_path / "bin" / "cublas64_13.dll").exists()  # the good one is kept for next time


def test_no_connection_or_no_room_is_said_plainly(
    tmp_path: Path, wheels: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ModelError, match="internet connection"):
        CudaLibraries(tmp_path, FakeDownloader(wheels, fail=True)).ensure()

    def full(path: object) -> SimpleNamespace:
        return SimpleNamespace(free=10)

    monkeypatch.setattr(cuda.shutil, "disk_usage", full)
    with pytest.raises(ModelError, match="GB free"):
        CudaLibraries(tmp_path / "other", FakeDownloader(wheels)).ensure()


def test_removing(tmp_path: Path, wheels: dict[str, bytes]) -> None:
    libraries = CudaLibraries(tmp_path / "cuda", FakeDownloader(wheels))
    assert not libraries.remove()
    libraries.ensure()
    assert libraries.remove()
    assert not libraries.ready()


def test_the_pinned_wheels_match_onnxruntime_gpus_needs() -> None:
    names = {w.name.split()[0] for w in cuda.WHEELS}
    assert names == {
        "nvidia-cublas",
        "nvidia-cuda-nvrtc",
        "nvidia-cuda-runtime",
        "nvidia-cudnn-cu13",
        "nvidia-cufft",
        "nvidia-curand",
        "nvidia-nvjitlink",
    }
    assert all(w.url.startswith("https://files.pythonhosted.org/") for w in cuda.WHEELS)
    assert 1.0e9 < cuda.DOWNLOAD_BYTES < 1.5e9


# --- tycho gpu, and the hint -------------------------------------------------------------------


@pytest.fixture
def gpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An NVIDIA card, as an installed Tycho sees it: no CUDA pip wheels."""
    import importlib.util

    state = SimpleNamespace(driver=617)
    monkeypatch.setattr(app.paths, "cuda_dir", lambda: tmp_path / "cuda")
    monkeypatch.setattr("tycho.stt.nvidia_driver", lambda: state.driver)
    real = importlib.util.find_spec

    def no_wheels(name: str, package: str | None = None) -> Any:
        return None if name == "nvidia" else real(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", no_wheels)
    return state


def test_tycho_gpu_status_and_remove(
    gpu: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert app.gpu_command("status") == 1
    assert "Not installed (1.2 GB" in capsys.readouterr().out
    assert app.gpu_command("remove") == 0
    assert "Nothing to remove" in capsys.readouterr().out


@pytest.mark.parametrize(("driver", "says"), [(None, "No NVIDIA"), (575, "580 or newer")])
def test_tycho_gpu_install_needs_an_nvidia_card_and_driver(
    gpu: SimpleNamespace, driver: int | None, says: str, capsys: pytest.CaptureFixture[str]
) -> None:
    gpu.driver = driver
    assert app.gpu_command("install") == 1
    assert says in capsys.readouterr().out


def test_the_hint_says_what_to_do(gpu: SimpleNamespace) -> None:
    hint = app.gpu_hint()
    assert hint is not None
    assert "uv run tycho gpu install" in hint
    gpu.driver = 560
    assert "driver 580" in (app.gpu_hint() or "")
    gpu.driver = None
    assert app.gpu_hint() is None


def test_without_any_cuda_libraries_cuda_isnt_available(monkeypatch: pytest.MonkeyPatch) -> None:
    # The installed app before `tycho gpu install`: no folder, no pip wheels. ONNX
    # Runtime's GPU build would still list CUDA, and only print that it failed.
    from tycho.stt import cuda_available, device

    def nothing(name: str) -> None:
        return None

    monkeypatch.setattr(device.importlib.util, "find_spec", nothing)
    assert not cuda_available()


def test_the_hint_fires_when_cuda_is_missing_and_not_when_memory_is_short(
    gpu: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The #81 review: without the libraries CUDA is unavailable, so the device chosen
    # is already the processor; the hint must still fire then.
    import logging

    from tycho.settings import Settings
    from tycho.stt import DeviceChoice
    from tycho.stt.parakeet import ParakeetTranscriber

    def loaded(folder: Path, device: str) -> Any:
        return SimpleNamespace(device="cpu")

    def present(self: object, spec: object, progress: object = None) -> Path:
        return Path()

    def no_cuda(current: Settings) -> DeviceChoice:
        return DeviceChoice("cpu", "CUDA isn't available")

    monkeypatch.setattr(app.ModelStore, "ensure", present)
    monkeypatch.setattr(ParakeetTranscriber, "load", loaded)
    monkeypatch.setattr(app, "device_choice", no_cuda)
    caplog.set_level(logging.INFO)
    app.load_transcriber(Settings())
    assert "uv run tycho gpu install" in caplog.text

    caplog.clear()
    app.load_transcriber(Settings(transcription_device="cpu"))  # the player chose the processor
    assert "gpu install" not in caplog.text


def test_removing_while_tycho_runs_is_refused(
    tmp_path: Path,
    wheels: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A running Tycho has the DLLs loaded (audit, 2026-11-03).
    from tycho.runtime.instance import SingleInstance

    libraries = CudaLibraries(tmp_path / "cuda", FakeDownloader(wheels))
    libraries.ensure()
    monkeypatch.setattr(app.paths, "cuda_dir", lambda: tmp_path / "cuda")
    with SingleInstance():  # this test plays the running Tycho
        assert app.gpu_command("remove") == 2
    assert "Close it first" in capsys.readouterr().err
    assert libraries.ready()
    assert app.gpu_command("remove") == 0
    assert not libraries.ready()


def test_a_removal_that_fails_says_so_plainly(
    tmp_path: Path, wheels: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    libraries = CudaLibraries(tmp_path / "cuda", FakeDownloader(wheels))
    libraries.ensure()

    def in_use(path: Path) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(cuda.shutil, "rmtree", in_use)
    with pytest.raises(ModelError, match="Close anything using it"):
        libraries.remove()
