"""Starting Tycho from the Start menu (#146): no terminal, and a second start opens the
running Tycho's window instead of saying it's already running."""

# Stand-ins for app functions are lambdas here.
# pyright: reportUnknownLambdaType=false, reportUnknownArgumentType=false

import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tycho import app
from tycho.runtime import AlreadyRunning
from tycho.runtime.instance import OpenRequests, ask_to_open
from tycho.settings import Settings

ROOT = Path(__file__).resolve().parent.parent


def _name() -> str:
    return rf"Local\tycho-ai-open-test-{time.monotonic_ns()}"


def test_a_second_start_reaches_the_running_tycho() -> None:
    name = _name()
    assert ask_to_open(name) is False  # nobody listening
    asked = threading.Event()
    with OpenRequests(name) as requests:
        requests.target = asked.set
        assert ask_to_open(name) is True
        assert asked.wait(2.0)
    assert ask_to_open(name) is False  # gone with the running Tycho


def test_a_failing_target_doesnt_stop_the_waiting(caplog: pytest.LogCaptureFixture) -> None:
    name = _name()
    asked = threading.Event()
    calls: list[int] = []

    def target() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the window was closing")
        asked.set()

    with OpenRequests(name) as requests:
        requests.target = target
        ask_to_open(name)
        deadline = time.monotonic() + 2.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        ask_to_open(name)
        assert asked.wait(2.0)
    assert "Couldn't show Tycho's window" in caplog.text


class _Running:
    """A SingleInstance that finds another Tycho already running."""

    def __enter__(self) -> None:
        raise AlreadyRunning("Tycho is already running. Close the other one first.")

    def __exit__(self, *_exc: object) -> None:
        pass


@pytest.mark.parametrize(("listening", "code"), [(True, 0), (False, 1)])
def test_starting_tycho_again_opens_the_running_ones_window(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    listening: bool,
    code: int,
) -> None:
    import tycho.runtime
    import tycho.runtime.instance

    monkeypatch.setattr(app.logs, "setup", lambda verbose=False: tmp_path / "tycho.jsonl")
    monkeypatch.setattr(tycho.runtime, "SingleInstance", _Running)
    monkeypatch.setattr(tycho.runtime.instance, "ask_to_open", lambda: listening)
    assert app.run_command(Settings()) == code
    out = capsys.readouterr()
    if listening:
        assert "its window is open" in out.out
    else:
        assert "already running" in out.err  # a terminal-only Tycho can't be asked


class _Requests:
    target: Any = None


class _Window:
    def __init__(self, requests: _Requests) -> None:
        self.requests = requests
        self.target_while_open: Any = None

    def open(self) -> bool:
        self.target_while_open = self.requests.target
        return True

    def focus(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_a_second_start_during_setup_brings_setup_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _Requests()
    window = _Window(requests)
    monkeypatch.setattr(app, "app_window", lambda **_options: window)
    app.first_run(requests)  # type: ignore[arg-type]
    assert window.target_while_open == window.focus
    assert requests.target is not window.focus  # not once setup has closed


@pytest.fixture
def windowed(monkeypatch: pytest.MonkeyPatch) -> None:
    import tycho.desktop.setup

    monkeypatch.setattr(app, "WINDOWED", True)
    monkeypatch.setattr(app, "start_update_check", lambda _current: None)
    monkeypatch.setattr(tycho.desktop.setup, "needs_setup", lambda _current: True)


@pytest.mark.usefixtures("windowed")
def test_with_no_terminal_a_missing_webview_is_explained_not_asked_in_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(app, "first_run", lambda _requests: "no_window")
    assert app._run(Settings(), tmp_path / "log") == 1  # pyright: ignore[reportPrivateUsage]
    assert "WebView2" in capsys.readouterr().err


@pytest.mark.usefixtures("windowed")
def test_with_no_terminal_no_button_is_explained(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import tycho.desktop.setup

    monkeypatch.setattr(tycho.desktop.setup, "needs_setup", lambda _current: False)
    monkeypatch.setattr(app.settings, "load", lambda: Settings())
    assert app._run(Settings(), tmp_path / "log") == 1  # pyright: ignore[reportPrivateUsage]
    assert "push-to-talk" in capsys.readouterr().err


def _entry_point() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "tycho_main_under_test", ROOT / "packaging" / "tycho_main.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_windowed_app_shows_a_problem_in_a_message_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    entry = _entry_point()
    shown: list[str] = []

    def fails(argv: list[str]) -> int:
        assert argv == ["run"]  # the Start menu passes nothing: run
        for n in range(500):
            print(f"Listening line {n}")  # a long session: only the end is kept
        print("Tycho couldn't find the game.", file=sys.stderr)
        return 1

    class User32:
        @staticmethod
        def MessageBoxW(_owner: object, text: str, title: str, _flags: int) -> int:
            shown.append(f"{title}: {text}")
            return 1

    monkeypatch.setattr(app, "main", fails)
    monkeypatch.setattr(ctypes, "windll", type("Windll", (), {"user32": User32})())
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # restored after the test
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    assert entry.windowed([]) == 1
    assert len(shown) == 1
    assert "Tycho couldn't find the game." in shown[0]
    assert "Listening line 0" not in shown[0]
    assert "The log is in" in shown[0]


def test_the_windowed_app_is_quiet_when_tycho_quits_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    entry = _entry_point()
    shown: list[str] = []
    monkeypatch.setattr(app, "main", lambda _argv: 0)
    monkeypatch.setattr(
        ctypes,
        "windll",
        type(
            "Windll", (), {"user32": type("U", (), {"MessageBoxW": lambda *a: shown.append("x")})}
        )(),
    )
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    assert entry.windowed([]) == 0
    assert shown == []
