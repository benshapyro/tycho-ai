import hashlib
import http.server
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tycho.stt import ModelError, ModelFile, ModelSpec, ModelStore, choose_device
from tycho.stt.models import HttpDownloader
from tycho.stt.parakeet import Device, device_in_use

CONTENT = {"a.onnx": b"weights" * 100, "vocab.txt": b"hello\nworld\n"}


def _spec(content: dict[str, bytes] = CONTENT) -> ModelSpec:
    return ModelSpec(
        name="test-model",
        repo="someone/test-model",
        revision="abc123",
        files=tuple(
            ModelFile(name, len(data), hashlib.sha256(data).hexdigest())
            for name, data in content.items()
        ),
    )


class FakeDownloader:
    """Serves ``CONTENT`` by file name, optionally corrupted or failing."""

    def __init__(self, serve: dict[str, bytes] = CONTENT, fail: bool = False) -> None:
        self.serve = serve
        self.fail = fail
        self.fetched: list[str] = []

    def fetch(self, url: str, dest: Path, progress: Callable[[int, int], None]) -> None:
        name = url.rsplit("/", 1)[-1]
        self.fetched.append(name)
        if self.fail:
            raise OSError("connection reset")
        data = self.serve[name]
        dest.write_bytes(data)
        progress(len(data), len(data))


def test_downloads_what_is_missing_then_works_offline(tmp_path: Path) -> None:
    spec, downloader = _spec(), FakeDownloader()
    store = ModelStore(tmp_path, downloader)
    assert not store.ready(spec)
    folder = store.ensure(spec)
    assert (folder / "a.onnx").read_bytes() == CONTENT["a.onnx"]
    assert sorted(downloader.fetched) == ["a.onnx", "vocab.txt"]
    assert store.ready(spec)

    offline = ModelStore(tmp_path, FakeDownloader(fail=True))
    assert offline.ensure(spec) == folder  # nothing to fetch, so no network needed


def test_the_url_is_pinned_to_a_revision() -> None:
    spec = _spec()
    assert spec.url(spec.files[0]) == (
        "https://huggingface.co/someone/test-model/resolve/abc123/a.onnx"
    )


def test_a_corrupted_download_is_deleted_and_reported(tmp_path: Path) -> None:
    tampered = {**CONTENT, "a.onnx": b"WEIGHTS" * 100}  # same size, different bytes
    store = ModelStore(tmp_path, FakeDownloader(serve=tampered))
    with pytest.raises(ModelError, match="checksum"):
        store.ensure(_spec())
    assert not (store.folder(_spec()) / "a.onnx").exists()
    assert not (store.folder(_spec()) / "a.onnx.part").exists()


def test_a_file_changed_on_disk_is_fetched_again(tmp_path: Path) -> None:
    spec = _spec()
    store = ModelStore(tmp_path, FakeDownloader())
    folder = store.ensure(spec)
    vocab = folder / "vocab.txt"
    before = vocab.stat().st_mtime_ns
    vocab.write_bytes(b"HELLO\nWORLD\n")  # same size, new content
    os.utime(vocab, ns=(before + 1_000_000_000, before + 1_000_000_000))  # and a new time
    assert not store.ready(spec)
    assert [f.name for f in store.missing(spec)] == ["vocab.txt"]
    downloader = FakeDownloader()
    ModelStore(tmp_path, downloader).ensure(spec)
    assert downloader.fetched == ["vocab.txt"]


def test_files_already_present_are_hashed_once_not_downloaded(tmp_path: Path) -> None:
    spec = _spec()
    folder = tmp_path / spec.folder
    folder.mkdir(parents=True)
    for name, data in CONTENT.items():
        (folder / name).write_bytes(data)  # e.g. copied over from the benchmark tools
    downloader = FakeDownloader()
    ModelStore(tmp_path, downloader).ensure(spec)
    assert downloader.fetched == []
    assert ModelStore(tmp_path).ready(spec)


def test_a_failed_download_gives_a_plain_message(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="internet connection"):
        ModelStore(tmp_path, FakeDownloader(fail=True)).ensure(_spec())


# --- The real downloader, against a local server -----------------------------------


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    data = CONTENT["a.onnx"]
    ranges: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            start = 0
            if header := self.headers.get("Range"):
                ranges.append(header)
                start = int(header.removeprefix("bytes=").rstrip("-"))
                self.send_response(206)
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            self.wfile.write(data[start:])

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/a.onnx", ranges
    httpd.shutdown()
    httpd.server_close()


def test_the_downloader_resumes_a_partial_file(
    tmp_path: Path, server: tuple[str, list[str]]
) -> None:
    url, ranges = server
    part = tmp_path / "a.onnx.part"
    part.write_bytes(CONTENT["a.onnx"][:250])
    seen: list[tuple[int, int]] = []
    HttpDownloader(allow_http=True).fetch(url, part, lambda done, total: seen.append((done, total)))
    assert part.read_bytes() == CONTENT["a.onnx"]
    assert ranges == ["bytes=250-"]
    assert seen[-1] == (700, 700)


def test_the_downloader_refuses_plain_http_by_default(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="insecure"):
        HttpDownloader().fetch("http://example.com/a.onnx", tmp_path / "a", lambda d, t: None)


# --- Where recognition runs ----------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "cuda", "free_mb", "device", "reason"),
    [
        ("auto", True, 12_000, "gpu", "11.7 GB free"),
        ("auto", True, 3_000, "cpu", "needs 4.4 GB"),
        ("auto", True, None, "cpu", "couldn't read"),
        ("auto", False, 12_000, "cpu", "CUDA isn't available"),
        ("cpu", True, 12_000, "cpu", "settings"),
        ("gpu", True, 1_000, "gpu", "settings"),  # the player knows best
        ("gpu", False, None, "cpu", "but CUDA isn't available"),
    ],
)
def test_device_choice(
    setting: str, cuda: bool, free_mb: int | None, device: str, reason: str
) -> None:
    choice = choose_device(setting, cuda=cuda, free_mb=free_mb)  # pyright: ignore[reportArgumentType]
    assert choice.device == device
    assert reason in choice.reason


@pytest.mark.parametrize(
    ("providers", "asked", "running"),
    [
        (["CUDAExecutionProvider", "CPUExecutionProvider"], "gpu", "gpu"),
        (["CPUExecutionProvider"], "gpu", "cpu"),  # CUDA silently didn't load
        (["CPUExecutionProvider"], "cpu", "cpu"),
    ],
)
def test_the_device_reported_is_the_one_really_used(
    providers: list[str], asked: Device, running: Device
) -> None:
    session = SimpleNamespace(get_providers=lambda: providers)
    model = SimpleNamespace(asr=SimpleNamespace(_encoder=session))
    assert device_in_use(model, asked) == running


def test_an_unrecognised_model_is_taken_at_its_word() -> None:
    assert device_in_use(object(), "gpu") == "gpu"
