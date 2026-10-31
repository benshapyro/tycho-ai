"""Downloading speech models once, checking them, and working offline after (ADR 6, ADR 11).

Each model is pinned to an exact revision, and every file to its SHA-256, so
what runs on a player's PC is byte-for-byte what was benchmarked. Hashing a
2.4 GB file takes seconds, so once a file has been checked its size and
modification time are recorded, and it's only hashed again if either changes.
"""

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_CHUNK = 1 << 20
_MARKER = ".tycho-verified.json"

type Progress = Callable[[str, int, int], None]
"""Called as (file name, bytes so far, total bytes) while downloading."""


class ModelError(Exception):
    """A model couldn't be downloaded or failed its check, with a message a player can act on."""


@dataclass(frozen=True, slots=True)
class ModelFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    """The name onnx-asr knows the model by."""
    repo: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def folder(self) -> str:
        return self.repo

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def url(self, file: ModelFile) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{file.name}"


PARAKEET_V2 = ModelSpec(
    name="nemo-parakeet-tdt-0.6b-v2",
    repo="istupakov/parakeet-tdt-0.6b-v2-onnx",
    revision="0bbb45a3365852604aef28b538a8f066f4ccaa85",
    files=(
        ModelFile(
            "config.json", 97, "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"
        ),
        ModelFile(
            "vocab.txt", 9384, "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"
        ),
        ModelFile(
            "nemo128.onnx",
            139_764,
            "a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f",
        ),
        ModelFile(
            "decoder_joint-model.onnx",
            35_792_059,
            "cbb52a07bd70ab5b67f8439d4b3cd8704b18467b4430bcacb5adabe154b8d191",
        ),
        ModelFile(
            "encoder-model.onnx",
            41_770_866,
            "3987bcd28175d829d12888a996a84e8f62a0e374d9ffd640662c1515adc679d3",
        ),
        ModelFile(
            "encoder-model.onnx.data",
            2_435_420_160,
            "4dab7362d4874d85965045b1e41b2d61dd2cc0fb25671a7f6b3dc47bf120cc41",
        ),
    ),
)
"""The speech model Tycho uses (ADR 2 outcome): full precision, as benchmarked."""


class Downloader(Protocol):
    def fetch(self, url: str, dest: Path, progress: Callable[[int, int], None]) -> None:
        """Download ``url`` to ``dest``, continuing a partial ``dest`` if there is one."""
        ...


class HttpDownloader:
    """Plain HTTPS with resume. Satisfies ``Downloader``."""

    def __init__(self, *, timeout: float = 30.0, allow_http: bool = False) -> None:
        self._timeout = timeout
        self._schemes = ("https://", "http://") if allow_http else ("https://",)

    def fetch(self, url: str, dest: Path, progress: Callable[[int, int], None]) -> None:
        if not url.startswith(self._schemes):
            raise ModelError(f"Refusing to download over an insecure link: {url}")
        have = dest.stat().st_size if dest.exists() else 0
        request = urllib.request.Request(url, headers={"User-Agent": "tycho-ai"})  # noqa: S310 - scheme checked above
        if have:
            request.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            if have and response.status != 206:  # the server ignored the range: start over
                have = 0
            total = have + int(response.headers.get("Content-Length", 0))
            with dest.open("ab" if have else "wb") as out:
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
                    have += len(chunk)
                    progress(have, total)


class ModelStore:
    """The models folder: what's there, what's been checked, and getting what's missing."""

    def __init__(self, root: Path, downloader: Downloader | None = None) -> None:
        self.root = root
        self._downloader = downloader or HttpDownloader()

    def folder(self, spec: ModelSpec) -> Path:
        return self.root / spec.folder

    def missing(self, spec: ModelSpec) -> list[ModelFile]:
        """Files that aren't there, or don't match. Hashes only files that changed since checked."""
        folder = self.folder(spec)
        checked = _read_marker(folder)
        missing: list[ModelFile] = []
        for file in spec.files:
            if not self._verify(folder / file.name, file, checked):
                missing.append(file)
        _write_marker(folder, checked)
        return missing

    def ready(self, spec: ModelSpec) -> bool:
        """Present and checked, without hashing anything. For a quick status, not for loading."""
        folder = self.folder(spec)
        checked = _read_marker(folder)
        return all(checked.get(f.name) == [*_stamp(folder / f.name), f.sha256] for f in spec.files)

    def ensure(self, spec: ModelSpec, progress: Progress | None = None) -> Path:
        """The model's folder, downloading and checking whatever is missing first."""
        folder = self.folder(spec)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for file in self.missing(spec):
                self._download(spec, file, folder, progress)
        except OSError as e:
            raise ModelError(
                f"Couldn't download the speech model to {folder}: {e}. Check the internet "
                "connection and free disk space, then try again; finished files are kept."
            ) from e
        return folder

    def _download(
        self, spec: ModelSpec, file: ModelFile, folder: Path, progress: Progress | None
    ) -> None:
        target, part = folder / file.name, folder / f"{file.name}.part"
        target.unlink(missing_ok=True)
        if part.exists() and part.stat().st_size > file.size:
            part.unlink()

        def report(done: int, total: int) -> None:
            if progress is not None:
                progress(file.name, done, total)

        self._downloader.fetch(spec.url(file), part, report)
        if part.stat().st_size != file.size or _sha256(part) != file.sha256:
            part.unlink(missing_ok=True)
            raise ModelError(
                f"{file.name} didn't match its checksum after downloading, so it was deleted. "
                "Try again; if it keeps failing, the download may be blocked or altered."
            )
        os.replace(part, target)
        checked = _read_marker(folder)
        checked[file.name] = [*_stamp(target), file.sha256]
        _write_marker(folder, checked)

    def _verify(self, path: Path, file: ModelFile, checked: dict[str, list[object]]) -> bool:
        stamp = _stamp(path)
        if stamp[0] != file.size:
            checked.pop(file.name, None)
            return False
        if checked.get(file.name) == [*stamp, file.sha256]:
            return True
        if _sha256(path) != file.sha256:
            checked.pop(file.name, None)
            return False
        checked[file.name] = [*stamp, file.sha256]
        return True


def _stamp(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (-1, -1)
    return stat.st_size, stat.st_mtime_ns


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _read_marker(folder: Path) -> dict[str, list[object]]:
    try:
        data = json.loads((folder / _MARKER).read_text("utf-8"))
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _write_marker(folder: Path, checked: dict[str, list[object]]) -> None:
    if folder.is_dir():
        temp = folder / f"{_MARKER}.tmp"
        temp.write_text(json.dumps(checked, indent=1), "utf-8")
        os.replace(temp, folder / _MARKER)
