"""NVIDIA's CUDA and cuDNN libraries, downloaded on request so speech recognition can
use an NVIDIA graphics card (#56).

The base install runs speech recognition on the processor, on any PC: about 0.9 s
a command on the author's PC. With these libraries, an NVIDIA card does it in
under 0.1 s (77 ms; tools/bench, #80). They are about 1.2 GB to download and
1.6 GB on disk, so they're only fetched when the player asks (`tycho gpu install`).

They come from NVIDIA's own wheels on PyPI, pinned to the versions in uv.lock and
checked by hash; only their DLLs are kept. NVIDIA's licence for these libraries
lets applications redistribute and download them; they're never in the repository.
"""

import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tycho.stt.models import (
    Downloader,
    HttpDownloader,
    ModelError,
    Progress,
    fetch_whole,
    file_sha256,
    wrong_download,
)

log = logging.getLogger(__name__)

MIN_DRIVER = 580
"""CUDA 13 needs NVIDIA driver 580 or newer."""
MARKER = ".tycho-cuda.json"


@dataclass(frozen=True, slots=True)
class Wheel:
    name: str
    url: str
    size: int
    sha256: str


_PYPI = "https://files.pythonhosted.org/packages"
WHEELS = (
    Wheel(
        "nvidia-cublas 13.8.0.4",
        f"{_PYPI}/a3/df/f1246959833e2c437db8be3e5b477f66b87f8817821ed40de6c7561c9a36/nvidia_cublas-13.8.0.4-py3-none-win_amd64.whl",
        423_266_897,
        "8c5494423bb8a46822cb6b0cb95d7fa4be2d7b96a31155dff083839ec8297910",
    ),
    Wheel(
        "nvidia-cuda-nvrtc 13.4.92",
        f"{_PYPI}/64/5b/aa91896f64444eff1dd7f189f4db05046c7f0ba08c0f531283c1c1aac581/nvidia_cuda_nvrtc-13.4.92-py3-none-win_amd64.whl",
        46_972_283,
        "6af7ac5372920f6a7a560d0699348560fe44cb52c1d722d6afd3119f8af948c4",
    ),
    Wheel(
        "nvidia-cuda-runtime 13.4.92",
        f"{_PYPI}/86/00/d5436004268f049214193659ebc36550b5ef3925c3d13b4cc980e13be6f5/nvidia_cuda_runtime-13.4.92-py3-none-win_amd64.whl",
        2_778_543,
        "08dca5e4aba480c2fd5b55075c0fa71b84ef9dcf0521f2d58baa14a803a7311c",
    ),
    Wheel(
        "nvidia-cudnn-cu13 9.27.0.42",
        f"{_PYPI}/87/6a/e55ff0ac26a5c6e2b21f41c9d04ad096b4ed6da593fba7e25845c61b0532/nvidia_cudnn_cu13-9.27.0.42-py3-none-win_amd64.whl",
        436_469_905,
        "7d96f634adafd55c72231eb0500ca77ab109ec8ebff7b33000b76e081bc4558e",
    ),
    Wheel(
        "nvidia-cufft 12.4.0.43",
        f"{_PYPI}/7b/cc/be7fe31058127336a66c88414a2ecc6beecf680baf85994a43eaf292b202/nvidia_cufft-12.4.0.43-py3-none-win_amd64.whl",
        160_953_139,
        "4ff7075f2d0b5f69291f70938d37a86ec632cbe5747184c74ba1f50f17accacc",
    ),
    Wheel(
        "nvidia-curand 10.4.4.72",
        f"{_PYPI}/e2/d4/f59ab342b82ff05ff27d674acd470ba625d788bf71338f7da6c89669d6b9/nvidia_curand-10.4.4.72-py3-none-win_amd64.whl",
        56_731_059,
        "e0bce83e083ef25976ee74f59e8f067c15149a74538f1be6c2462e286e7c9c68",
    ),
    Wheel(
        "nvidia-nvjitlink 13.4.92",
        f"{_PYPI}/72/7c/c44bd7277afafed9de262a23baec4dc8dcf7ecbb1b9ecb228821442d1e9b/nvidia_nvjitlink-13.4.92-py3-none-win_amd64.whl",
        39_053_950,
        "b286f3a4f227a9363efdec263c7b91788cef1478d2b8a5fa8bab7f3e82ff82fd",
    ),
)
"""The wheels onnxruntime-gpu 1.31.1 needs, as pinned in uv.lock. Update both together."""

DOWNLOAD_BYTES = sum(w.size for w in WHEELS)
SPACE_NEEDED = 2 * DOWNLOAD_BYTES
"""Each wheel and its unpacked DLLs exist side by side for a moment."""


class CudaLibraries:
    """The folder of CUDA DLLs: whether they're there, getting them, and removing them."""

    def __init__(self, root: Path, downloader: Downloader | None = None) -> None:
        self.root = root
        self._downloader = downloader or HttpDownloader(timeout=60.0)

    @property
    def dlls(self) -> Path:
        return self.root / "bin"

    def ready(self) -> bool:
        """All the pinned wheels unpacked, by the marker; no hashing."""
        try:
            done = json.loads((self.root / MARKER).read_text("utf-8"))
        except OSError, ValueError:
            return False
        return done == [w.sha256 for w in WHEELS] and any(self.dlls.glob("*.dll"))

    def ensure(self, progress: Progress | None = None) -> Path:
        """Download and unpack whatever is missing. Raises ``ModelError`` in plain words."""
        if self.ready():
            return self.dlls
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.root).free < SPACE_NEEDED:
                raise ModelError(
                    f"The graphics card libraries need about {SPACE_NEEDED / 1e9:.1f} GB free "
                    f"in {self.root}; free some space and try again."
                )
            self.dlls.mkdir(exist_ok=True)
            done = self._done()
            for wheel in WHEELS:
                if wheel.sha256 not in done:
                    self._fetch(wheel, progress)
                    done.append(wheel.sha256)
                    self._mark(done)
        except OSError as e:
            raise ModelError(
                f"Couldn't download the graphics card libraries to {self.root}: {e}. Check the "
                "internet connection and free disk space, then try again; finished parts are kept."
            ) from e
        self._mark([w.sha256 for w in WHEELS])
        return self.dlls

    def remove(self) -> bool:
        """Delete the libraries. Returns False if there were none."""
        if not self.root.exists():
            return False
        try:
            shutil.rmtree(self.root)
        except OSError as e:  # a DLL in use: tycho gpu remove checks Tycho isn't running
            raise ModelError(
                f"Couldn't remove all of {self.root} ({e.strerror or e}). Close anything using "
                "it, then try again."
            ) from e
        return True

    def _fetch(self, wheel: Wheel, progress: Progress | None) -> None:
        part = self.root / f"{wheel.sha256[:16]}.whl.part"
        if part.exists() and part.stat().st_size > wheel.size:
            part.unlink()

        def report(done: int, total: int) -> None:
            if progress is not None:
                progress(wheel.name, done, total)

        fetch_whole(self._downloader, wheel.url, part, wheel.size, report, wheel.name)
        if part.stat().st_size != wheel.size or file_sha256(part) != wheel.sha256:
            part.unlink(missing_ok=True)
            raise ModelError(wrong_download(wheel.name))
        with zipfile.ZipFile(part) as archive:
            for member in archive.infolist():
                name = Path(member.filename).name  # flattened: never a path out of the folder
                if name.lower().endswith(".dll"):
                    target = self.dlls / name
                    with archive.open(member) as source, target.open("wb") as out:
                        shutil.copyfileobj(source, out)
        part.unlink()
        log.info("Graphics card libraries: unpacked %s", wheel.name)

    def _done(self) -> list[str]:
        try:
            done = json.loads((self.root / MARKER).read_text("utf-8"))
        except OSError, ValueError:
            return []
        if not isinstance(done, list):
            return []
        return [h for h in cast(list[object], done) if isinstance(h, str)]

    def _mark(self, done: list[str]) -> None:
        partial = self.root / f"{MARKER}.partial"
        partial.write_text(json.dumps(done), "utf-8")
        os.replace(partial, self.root / MARKER)
