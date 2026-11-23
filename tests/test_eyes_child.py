"""The capture's child process (#558): a crash copying a frame of the game window ends
only the child, and Tycho says it can't see the window instead of stopping."""

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from tycho.eyes import CaptureError, Picture, child

PICTURE = Picture(b"\xff\xd8 a jpeg", 160, 90)
ACCESS_VIOLATION = 0xC0000005  # how Windows reports the crash in #558


def _ran(code: int, out: bytes = b"", err: bytes = b"") -> Any:
    def run(args: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        assert options["capture_output"]
        assert options["timeout"] == child.SECONDS
        assert "creationflags" in options  # no console window flashing over the game
        return subprocess.CompletedProcess(args, code, out, err)

    return run


def test_the_picture_comes_back_in_memory() -> None:
    assert child.take(1234, _ran(0, child.write(PICTURE))) == PICTURE


def test_a_crash_in_the_child_is_a_capture_error_not_the_end_of_tycho() -> None:
    crashed = _ran(ACCESS_VIOLATION, err=b"Windows fatal exception: access violation")
    with pytest.raises(CaptureError, match="0xc0000005"):
        child.take(1234, crashed)
    with pytest.raises(CaptureError, match="0xc0000005"):  # as a signed exit code
        child.take(1234, _ran(ACCESS_VIOLATION - 2**32))


def test_no_picture_says_why() -> None:
    with pytest.raises(CaptureError, match="Windows sent no picture"):
        child.take(
            1234, _ran(child.NO_PICTURE, err=b"Windows sent no picture of the game window\n")
        )


def test_a_child_that_hangs_or_cant_start_is_a_capture_error() -> None:
    def hangs(args: list[str], **_: Any) -> Any:
        raise subprocess.TimeoutExpired(args, child.SECONDS)

    def missing(args: list[str], **_: Any) -> Any:
        raise FileNotFoundError(args[0])

    for run in (hangs, missing):
        with pytest.raises(CaptureError):
            child.take(1234, run)
    with pytest.raises(CaptureError, match="no picture"):
        child.take(1234, _ran(0, b""))


def test_the_installed_app_runs_its_terminal_program_and_source_runs_the_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert child.command(42) == [sys.executable, "-m", "tycho.eyes.child", "42"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(Path("C:/Tycho/tycho-app.exe")))
    assert child.command(42) == [str(Path("C:/Tycho/tycho.exe")), child.FLAG, "42"]


def _child(argv: list[str], frame: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[int, bytes, str]:
    out, err = io.BytesIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=out, flush=lambda: None))
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(child.faulthandler, "enable", lambda: None)
    from tycho import win32

    monkeypatch.setattr(win32, "no_crash_box", lambda: None)  # not the test run's own
    return child.main(argv, frame=frame), out.getvalue(), err.getvalue()


def _blank(width: int, height: int) -> Any:
    def frame(_: int) -> Image.Image:
        return Image.new("RGBA", (width, height))

    return frame


def test_the_child_writes_one_small_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    code, sent, _ = _child(["7"], _blank(3840, 2160), monkeypatch)
    assert code == 0
    picture = child.read(sent)
    assert picture.data[:2] == b"\xff\xd8"
    assert max(picture.width, picture.height) <= 1568


@pytest.mark.parametrize("error", [CaptureError("no frame"), ImportError("winrt"), OSError("x")])
def test_the_child_says_why_there_is_no_picture(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def frame(_: int) -> Image.Image:
        raise error

    code, sent, why = _child(["7"], frame, monkeypatch)
    assert code == child.NO_PICTURE
    assert sent == b""
    assert why.strip()


def test_a_bad_window_number_is_no_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _, _ = _child(["not a number"], _blank(9, 9), monkeypatch)
    assert code == child.NO_PICTURE


def test_the_installed_app_starts_the_child_before_anything_else() -> None:
    main = (Path(__file__).parents[1] / "packaging" / "tycho_main.py").read_text("utf-8")
    assert "child.FLAG" in main
    assert main.index("child.FLAG") < main.index("windowed(sys.argv[1:])")
