import hashlib
import http.server
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tycho.stt import ModelError, ModelFile, ModelSpec, ModelStore, choose_device, models
from tycho.stt.device import smi_path
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


def _free(monkeypatch: pytest.MonkeyPatch, free: int) -> None:
    def usage(path: object) -> SimpleNamespace:
        return SimpleNamespace(free=free)

    monkeypatch.setattr(models.shutil, "disk_usage", usage)


def test_no_room_for_the_model_is_said_before_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec()
    _free(monkeypatch, spec.size - 1)
    downloader = FakeDownloader()
    with pytest.raises(ModelError) as caught:
        ModelStore(tmp_path, downloader).ensure(spec)
    assert downloader.fetched == []
    message = str(caught.value)
    assert "The speech model needs about" in message
    assert str(tmp_path / spec.folder) in message
    assert "free some space" in message


def test_a_partial_download_only_needs_room_for_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec()
    folder = tmp_path / spec.folder
    folder.mkdir(parents=True)
    (folder / "a.onnx.part").write_bytes(CONTENT["a.onnx"][:500])
    _free(monkeypatch, spec.size - 500)
    ModelStore(tmp_path, FakeDownloader()).ensure(spec)
    assert ModelStore(tmp_path).ready(spec)


def test_a_model_already_there_needs_no_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec()
    ModelStore(tmp_path, FakeDownloader()).ensure(spec)
    _free(monkeypatch, 0)
    ModelStore(tmp_path, FakeDownloader(fail=True)).ensure(spec)


def test_sizes_are_said_in_units_a_player_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, 40_000_000)
    with pytest.raises(ModelError, match=r"about 2\.5 GB free in .*, and that drive has 40 MB;"):
        models.check_space(tmp_path, 2_500_000_000, "The speech model needs")


# --- The real downloader, against a local server -----------------------------------


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    data = CONTENT["a.onnx"]
    ranges: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        """Like a real file server. Under /plain/ it ignores Range, as some do."""

        def do_GET(self) -> None:
            start = 0
            header = self.headers.get("Range")
            if header and not self.path.startswith("/plain/"):
                ranges.append(header)
                start = int(header.removeprefix("bytes=").rstrip("-"))
                if start >= len(data):
                    self.send_response(416)  # nothing after that byte
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
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


def test_a_finished_part_file_is_complete_not_an_error(
    tmp_path: Path, server: tuple[str, list[str]]
) -> None:
    url, _ = server
    part = tmp_path / "a.onnx.part"
    part.write_bytes(CONTENT["a.onnx"])  # whole: the server answers 416 to the range
    HttpDownloader(allow_http=True).fetch(url, part, lambda done, total: None)
    assert part.read_bytes() == CONTENT["a.onnx"]


def test_a_server_that_ignores_the_range_starts_over(
    tmp_path: Path, server: tuple[str, list[str]]
) -> None:
    url, _ = server
    part = tmp_path / "a.onnx.part"
    part.write_bytes(CONTENT["a.onnx"][:250])
    plain = url.replace("/a.onnx", "/plain/a.onnx")
    HttpDownloader(allow_http=True).fetch(plain, part, lambda done, total: None)
    assert part.read_bytes() == CONTENT["a.onnx"]  # not the first 250 bytes twice


def test_a_whole_part_file_left_by_a_crash_is_used_without_a_download(tmp_path: Path) -> None:
    spec, downloader = _spec(), FakeDownloader(fail=True)  # offline: it mustn't need to fetch
    folder = tmp_path / spec.folder
    folder.mkdir(parents=True)
    for name, data in CONTENT.items():
        (folder / f"{name}.part").write_bytes(data)
    assert ModelStore(tmp_path, downloader).ensure(spec) == folder
    assert downloader.fetched == []
    assert (folder / "a.onnx").read_bytes() == CONTENT["a.onnx"]
    assert not list(folder.glob("*.part"))


def test_a_whole_but_wrong_part_file_is_downloaded_again(tmp_path: Path) -> None:
    spec, downloader = _spec(), FakeDownloader()
    folder = tmp_path / spec.folder
    folder.mkdir(parents=True)
    (folder / "a.onnx.part").write_bytes(b"x" * len(CONTENT["a.onnx"]))
    ModelStore(tmp_path, downloader).ensure(spec)
    assert "a.onnx" in downloader.fetched
    assert (folder / "a.onnx").read_bytes() == CONTENT["a.onnx"]


def test_the_downloader_refuses_plain_http_by_default(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="insecure"):
        HttpDownloader().fetch("http://example.com/a.onnx", tmp_path / "a", lambda d, t: None)


# --- Where recognition runs ----------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "cuda", "free_mb", "device", "reason"),
    [
        ("auto", True, 12_000, "gpu", "11.7 GB free"),
        ("auto", True, 3_000, "cpu", "needs 3.9 GB"),
        ("auto", True, 4_200, "gpu", "4.1 GB free"),  # enough with the lean CUDA settings
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


class DroppingDownloader:
    """Sends ``step`` bytes per connection, then the connection closes cleanly."""

    def __init__(self, data: bytes, step: int, raises: bool = False) -> None:
        self.data, self.step, self.raises = data, step, raises
        self.calls = 0

    def fetch(self, url: str, dest: Path, progress: Callable[[int, int], None]) -> None:
        self.calls += 1
        have = dest.stat().st_size if dest.exists() else 0
        with dest.open("ab") as out:
            out.write(self.data[have : have + self.step])
        if self.raises:
            raise OSError("connection reset")


@pytest.mark.parametrize("raises", [False, True])
def test_a_dropped_connection_continues_where_it_stopped(tmp_path: Path, raises: bool) -> None:
    # A clean close used to delete up to 2.4 GB as a checksum failure (audit, 2026-11-03).
    data = CONTENT["a.onnx"]
    spec = _spec({"a.onnx": data})
    downloader = DroppingDownloader(data, step=len(data) // 3 + 1, raises=raises)
    folder = ModelStore(tmp_path, downloader).ensure(spec)
    assert (folder / "a.onnx").read_bytes() == data
    assert downloader.calls == 3


def test_a_download_that_keeps_stopping_is_kept_for_next_time(tmp_path: Path) -> None:
    data = CONTENT["a.onnx"]
    spec = _spec({"a.onnx": data})
    with pytest.raises(ModelError, match="keeps stopping"):
        ModelStore(tmp_path, DroppingDownloader(data, step=10)).ensure(spec)
    part = tmp_path / spec.folder / "a.onnx.part"
    assert part.read_bytes() == data[:50]  # five tries of 10 bytes
    ModelStore(tmp_path, DroppingDownloader(data, step=len(data))).ensure(spec)
    assert (tmp_path / spec.folder / "a.onnx").read_bytes() == data


def test_a_download_that_gets_nowhere_is_deleted(tmp_path: Path) -> None:
    # Shorter than it should be, and a second try adds nothing: not the right file.
    spec = _spec()
    short = {**CONTENT, "a.onnx": b"short"}
    with pytest.raises(ModelError, match="checksum"):
        ModelStore(tmp_path, FakeDownloader(serve=short)).ensure(spec)
    assert not (tmp_path / spec.folder / "a.onnx.part").exists()


# --- Finding nvidia-smi ------------------------------------------------------------------


def _tool(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "nvidia-smi.exe"
    path.write_bytes(b"")
    return path


def test_nvidia_smi_is_never_taken_from_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A planted nvidia-smi.exe in Downloads ran on every start (audit, 2026-11-03).
    downloads = tmp_path / "Downloads"
    _tool(downloads)
    monkeypatch.chdir(downloads)
    assert smi_path({"PATH": os.pathsep.join([".", "Downloads", ""])}) is None


def test_nvidia_smi_comes_from_the_drivers_folder_first(tmp_path: Path) -> None:
    system32 = _tool(tmp_path / "Windows" / "System32")
    _tool(tmp_path / "elsewhere")
    environ = {"SystemRoot": str(tmp_path / "Windows"), "PATH": str(tmp_path / "elsewhere")}
    assert smi_path(environ) == system32


def test_nvidia_smi_from_an_absolute_path_folder(tmp_path: Path) -> None:
    tool = _tool(tmp_path / "NVSMI")
    assert smi_path({"PATH": str(tmp_path / "NVSMI")}) == tool
    assert smi_path({}) is None


# --- The graphics card failing (Ben's run, 2026-11-03) -----------------------------------


class _Model:
    """Stands in for onnx-asr's model: fails like CUDA did, or answers."""

    def __init__(self, fails: int = 0) -> None:
        self.fails = fails
        self.calls = 0

    def recognize(self, samples: object, sample_rate: int) -> str:
        self.calls += 1
        if self.fails:
            self.fails -= 1
            raise RuntimeError(
                "[ONNXRuntimeError] : 1 : FAIL : Non-zero status code returned while running "
                "Div node.\nCUDA error cudaErrorUnknown:unknown error"
            )
        return " hello "


def _opener(gpu: _Model, cpu: _Model, opened: list[str]) -> Callable[..., object]:
    from tycho.stt.parakeet import ParakeetTranscriber

    def _open(cls: type[ParakeetTranscriber], folder: Path, device: Device) -> ParakeetTranscriber:
        opened.append(device)
        return ParakeetTranscriber(gpu if device == "gpu" else cpu, device, folder)  # type: ignore[arg-type]

    return classmethod(_open)  # type: ignore[return-value]


def test_a_graphics_card_that_fails_the_warm_up_falls_back_to_the_processor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # 4.7 GB was free, but the warm-up ran out of memory and Tycho stopped with a traceback.
    from tycho.stt.parakeet import ParakeetTranscriber

    opened: list[str] = []
    monkeypatch.setattr(ParakeetTranscriber, "_open", _opener(_Model(fails=1), _Model(), opened))
    transcriber = ParakeetTranscriber.load(tmp_path, "gpu")
    assert transcriber.device == "cpu"
    assert opened == ["gpu", "cpu"]
    assert "using the processor instead" in caplog.text
    assert "cudaErrorUnknown" not in caplog.text  # one plain line, not ONNX Runtime's essay


def test_a_graphics_card_that_fails_mid_session_moves_to_the_processor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tycho.stt.parakeet import ParakeetTranscriber

    gpu, cpu = _Model(), _Model()
    opened: list[str] = []
    monkeypatch.setattr(ParakeetTranscriber, "_open", _opener(gpu, cpu, opened))
    transcriber = ParakeetTranscriber.load(tmp_path, "gpu")
    assert transcriber.device == "gpu"
    gpu.fails = 1  # the game took the card's memory
    assert transcriber.transcribe(np.zeros(16_000, dtype=np.float32), 16_000) == "hello"
    assert transcriber.device == "cpu"
    assert opened == ["gpu", "cpu"]
    transcriber.transcribe(np.zeros(16_000, dtype=np.float32), 16_000)
    assert cpu.calls == 2  # stays on the processor


def test_a_processor_failure_is_a_plain_model_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tycho.stt.parakeet import ParakeetTranscriber

    monkeypatch.setattr(ParakeetTranscriber, "_open", _opener(_Model(), _Model(fails=1), []))
    with pytest.raises(ModelError, match="Couldn't load the speech model"):
        ParakeetTranscriber.load(tmp_path, "cpu")


def test_the_graphics_card_gets_the_lean_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # They take the model from 3.5 GB to 3.0 GB at the same speed (tools/bench/gpu_memory.py).
    import onnx_asr

    from tycho.stt.parakeet import CUDA_OPTIONS, LEAN_CUDA, ParakeetTranscriber

    asked: list[object] = []

    def load_model(name: str, folder: Path, providers: list[object]) -> object:
        asked.append(providers)
        return _Model()

    monkeypatch.setattr(onnx_asr, "load_model", load_model)
    ParakeetTranscriber._open(tmp_path, "gpu")  # pyright: ignore[reportPrivateUsage]
    ParakeetTranscriber._open(tmp_path, "cpu")  # pyright: ignore[reportPrivateUsage]
    assert CUDA_OPTIONS == LEAN_CUDA
    assert asked == [
        [("CUDAExecutionProvider", dict(LEAN_CUDA)), "CPUExecutionProvider"],
        ["CPUExecutionProvider"],
    ]


def test_the_small_parts_can_run_on_the_processor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Off by default: slower with the card idle (298 against 240 ms), to try with the
    # game running (#58).
    import onnx_asr

    from tycho.stt import parakeet
    from tycho.stt.parakeet import SMALL_PARTS_ON_CPU, ParakeetTranscriber

    assert SMALL_PARTS_ON_CPU is False
    asked: list[dict[str, object]] = []
    moved: list[Path] = []

    def load_model(name: str, folder: Path, **kwargs: object) -> object:
        asked.append(kwargs)
        return _Model()

    def decoder_on_cpu(model: object, folder: Path) -> None:
        moved.append(folder)

    monkeypatch.setattr(onnx_asr, "load_model", load_model)
    monkeypatch.setattr(parakeet, "_decoder_on_cpu", decoder_on_cpu)
    ParakeetTranscriber._open(tmp_path, "gpu", small_parts_on_cpu=True)  # pyright: ignore[reportPrivateUsage]
    assert asked[-1]["preprocessor_config"] == {"use_numpy_preprocessors": True}
    assert moved == [tmp_path]
    ParakeetTranscriber._open(tmp_path, "gpu")  # pyright: ignore[reportPrivateUsage]
    assert "preprocessor_config" not in asked[-1]
    assert moved == [tmp_path]


def test_a_decoder_that_cant_be_found_is_left_where_it_is(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from tycho.stt import parakeet

    parakeet._decoder_on_cpu(_Model(), tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert "stays where it is" in caplog.text
