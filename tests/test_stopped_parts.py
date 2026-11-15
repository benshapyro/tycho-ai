"""The Tile or the tray icon stopping while Tycho runs is said once, and Tycho keeps
listening (#297)."""

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fakes.fake_display import FakeDisplay
from tests.fakes.fake_speaker import FakeSpeaker
from tests.test_runtime import Rig, rig, run
from tycho.desktop import shell
from tycho.desktop.tray import TrayIcon
from tycho.settings import Settings
from tycho.start.run import TILE_STOPPED
from tycho.tile import Card
from tycho.tile import window as tile_window
from tycho.tile.display import TileDisplay
from tycho.tile.window import GIVE_UP_AFTER, Frame, Screen, TileWindow

SAID = "Tycho's icon by the clock stopped working."


async def _told(r: Rig, said: str, *, thread: bool = False) -> None:
    """Run the session, tell it ``said`` (from another thread if asked), and stop."""
    stop = asyncio.Event()
    running = asyncio.create_task(r.session.run(stop))
    await asyncio.sleep(0.01)
    if thread:
        teller = threading.Thread(target=r.session.tell, args=(said,))
        teller.start()
        teller.join()
    else:
        r.session.tell(said)
    await asyncio.sleep(0.05)
    assert not running.done()  # it carries on
    stop.set()
    await running


# --- What the player is told -----------------------------------------------------------


def test_with_the_tile_alive_its_a_notice_card_and_nothing_is_said() -> None:
    speaker, display = FakeSpeaker(), FakeDisplay()
    r = rig(speaker=speaker, display=display)
    run(lambda: _told(r, SAID, thread=True))
    assert [(card.kind, card.body) for card in display.shown] == [("notice", (SAID,))]
    assert speaker.spoken == []
    assert r.feedback.played == []


def test_without_the_tile_its_said_aloud() -> None:
    speaker, display = FakeSpeaker(), FakeDisplay(on=False)
    r = rig(speaker=speaker, display=display)
    run(lambda: _told(r, SAID, thread=True))
    assert display.shown == []
    assert speaker.spoken == [SAID]


def test_with_no_tile_and_no_voice_its_the_not_done_sound_and_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    r = rig()
    with caplog.at_level("WARNING"):
        run(lambda: _told(r, SAID))
    assert r.feedback.played == ["not_done"]
    assert SAID in caplog.text


def test_the_session_still_answers_afterwards() -> None:
    async def test() -> Rig:
        r = rig("landing gear", display=FakeDisplay())
        r.session.tell(SAID)  # before it runs: logged, and nothing breaks
        r.say()
        await r.session.idle()
        await r.session.close()
        r.session.tell(SAID)  # after it closed: the same
        return r

    r = run(test)
    assert [x.status for x in r.results] == ["done"]


# --- The Tile ---------------------------------------------------------------------------


class _Window:
    """A Tile window that opens, then can be made to stop as the real one does."""

    def __init__(self, frame: Any) -> None:
        self.frame = frame
        self.failed = ""
        self.on_stopped: Any = None

    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_a_tile_whose_window_stopped_is_off_and_says_so_once() -> None:
    display = TileDisplay(Settings(), window=_Window)  # type: ignore[arg-type]
    whys: list[str] = []
    display.on_stopped = whys.append
    assert display.start()
    assert display.show(Card("notice", body=("Hello.",)))

    display._window.on_stopped("OSError: gone")  # pyright: ignore[reportPrivateUsage, reportOptionalCall]

    assert whys == ["OSError: gone"]
    assert not display.on
    # The session then says an answer instead of showing a card nobody would see.
    assert not display.show(Card("notice", body=("Hello again.",)))
    assert display.frame(Screen(0, 0, 1920, 1080)) is None


def test_a_dead_tile_is_told_aloud_through_the_session() -> None:
    # As `tycho run` wires it: the Tile is the session's display, and it just stopped.
    display = TileDisplay(Settings(), window=_Window)  # type: ignore[arg-type]
    speaker = FakeSpeaker()
    r = rig(speaker=speaker, display=display)  # type: ignore[arg-type]
    display.on_stopped = lambda _why: r.session.tell(TILE_STOPPED)

    async def test() -> None:
        stop = asyncio.Event()
        running = asyncio.create_task(r.session.run(stop))
        await asyncio.sleep(0.01)
        display._window.on_stopped("OSError: gone")  # pyright: ignore[reportPrivateUsage, reportOptionalCall]
        await asyncio.sleep(0.05)
        stop.set()
        await running

    run(test)
    assert speaker.spoken == [TILE_STOPPED]


def _window_that(loop: Any) -> tuple[TileWindow, list[str]]:
    window = TileWindow(lambda _screen: None)
    whys: list[str] = []
    window.on_stopped = whys.append
    window._loop = lambda: loop(window)  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return window, whys


def test_the_tiles_window_failing_after_it_opened_reports_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def loop(window: TileWindow) -> None:
        window._hwnd = 7  # pyright: ignore[reportPrivateUsage]
        window._ready.set()  # pyright: ignore[reportPrivateUsage]
        raise OSError("the desktop went away")

    window, whys = _window_that(loop)
    window._run()  # pyright: ignore[reportPrivateUsage]
    assert whys == ["OSError: the desktop went away"]
    assert window.failed == "OSError: the desktop went away"
    assert "The Tile is gone" in caplog.text


def test_a_tile_that_never_opened_isnt_reported_as_stopping() -> None:
    def loop(_window: TileWindow) -> None:
        raise OSError("RegisterClassExW failed")  # start() says why, and the Tile is off

    window, whys = _window_that(loop)
    window._run()  # pyright: ignore[reportPrivateUsage]
    assert whys == []
    assert window.failed == "OSError: RegisterClassExW failed"


def test_a_tile_closed_on_purpose_isnt_reported() -> None:
    def loop(window: TileWindow) -> None:
        window._hwnd = 7  # pyright: ignore[reportPrivateUsage]
        window._closing = True  # close() was called  # pyright: ignore[reportPrivateUsage]

    window, whys = _window_that(loop)
    window._run()  # pyright: ignore[reportPrivateUsage]
    assert whys == []
    assert window.failed == ""


def test_a_failing_report_doesnt_escape_the_tiles_thread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def loop(window: TileWindow) -> None:
        window._hwnd = 7  # pyright: ignore[reportPrivateUsage]
        raise OSError("gone")

    def report(_why: str) -> None:
        raise RuntimeError("nobody to tell")

    window, _ = _window_that(loop)
    window.on_stopped = report
    window._run()  # pyright: ignore[reportPrivateUsage]
    assert "Couldn't say the Tile stopped" in caplog.text


class _User32:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def DestroyWindow(self, _hwnd: int) -> bool:
        self.calls.append("destroy")
        return True

    def ShowWindow(self, _hwnd: int, how: int) -> bool:
        self.calls.append(f"show {how}")
        return True


def test_a_tile_that_cant_draw_gives_up_after_a_second_not_never(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # It logged a traceback fifteen times a second for as long as Tycho ran, and the
    # player saw an empty corner with no reason.
    user32 = _User32()
    monkeypatch.setattr(tile_window.win32, "user32", lambda: user32)
    monkeypatch.setattr(tile_window, "screen_of_foreground", lambda: Screen(0, 0, 1920, 1080))

    def frame(_screen: Screen) -> Frame:
        raise ValueError("a card that can't be drawn")

    window = TileWindow(frame)
    window._hwnd = 7  # pyright: ignore[reportPrivateUsage]
    for _ in range(GIVE_UP_AFTER - 1):
        window._tick()  # pyright: ignore[reportPrivateUsage]
    assert user32.calls == []
    window._tick()  # pyright: ignore[reportPrivateUsage]
    assert user32.calls == ["destroy"]  # the loop ends, and _run reports it
    assert window.failed == "it couldn't draw: ValueError: a card that can't be drawn"
    assert caplog.text.count("Drawing the Tile failed") == 1


def test_one_bad_frame_doesnt_stop_the_tile(monkeypatch: pytest.MonkeyPatch) -> None:
    user32 = _User32()
    monkeypatch.setattr(tile_window.win32, "user32", lambda: user32)
    monkeypatch.setattr(tile_window, "screen_of_foreground", lambda: Screen(0, 0, 1920, 1080))
    bad = [True] * (GIVE_UP_AFTER - 1) + [False] + [True] * (GIVE_UP_AFTER - 1)

    def frame(_screen: Screen) -> Frame:
        if bad.pop(0):
            raise ValueError("once")
        return None

    window = TileWindow(frame)
    window._hwnd = 7  # pyright: ignore[reportPrivateUsage]
    for _ in range(2 * GIVE_UP_AFTER - 1):
        window._tick()  # pyright: ignore[reportPrivateUsage]
    assert "destroy" not in user32.calls
    assert window.failed == ""


# --- The tray icon ----------------------------------------------------------------------


def _tray_that(loop: Any) -> tuple[TrayIcon, list[str]]:
    tray = TrayIcon(lambda: "Tycho", list, lambda _key: None)
    whys: list[str] = []
    tray.on_stopped = whys.append
    tray._loop = lambda: loop(tray)  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return tray, whys


def test_the_tray_failing_after_it_showed_reports_once(caplog: pytest.LogCaptureFixture) -> None:
    def loop(tray: TrayIcon) -> None:
        tray._hwnd = 7  # pyright: ignore[reportPrivateUsage]
        tray._ready.set()  # pyright: ignore[reportPrivateUsage]
        raise OSError("Explorer took it")

    tray, whys = _tray_that(loop)
    tray._run()  # pyright: ignore[reportPrivateUsage]
    assert whys == ["OSError: Explorer took it"]
    assert "The tray icon is gone" in caplog.text


def test_a_tray_whose_loop_just_ends_reports_too() -> None:
    def loop(tray: TrayIcon) -> None:
        tray._hwnd = 7  # pyright: ignore[reportPrivateUsage]

    tray, whys = _tray_that(loop)
    tray._run()  # pyright: ignore[reportPrivateUsage]
    assert whys == ["its message loop ended"]


def test_a_tray_that_never_showed_or_was_closed_isnt_reported() -> None:
    def never(_tray: TrayIcon) -> None:
        raise OSError("RegisterClassExW failed")

    def closed(tray: TrayIcon) -> None:
        tray._hwnd = 7  # pyright: ignore[reportPrivateUsage]
        tray._closing = True  # pyright: ignore[reportPrivateUsage]

    for loop in (never, closed):
        tray, whys = _tray_that(loop)
        tray._run()  # pyright: ignore[reportPrivateUsage]
        assert whys == []


class _Tray:
    """A tray icon that shows, then stops while Tycho is running."""

    failed = ""

    def __init__(self, *_args: object) -> None:
        self.on_stopped: Any = None
        _Tray.made = self

    made: Any = None

    def start(self) -> bool:
        return True

    def refresh(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_tycho_tells_the_player_the_tray_stopped_and_keeps_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tycho.desktop.host
    import tycho.desktop.tray

    told: list[str] = []
    served: list[str] = []

    class Host:
        restarting = False

        def __init__(self, _voice: object) -> None:
            self.on_quit: Any = None

        def restart(self) -> bool:
            return False

        def run(self, _open: object) -> None:
            _Tray.made.on_stopped("OSError: gone")  # while the voice loop runs
            served.append("still serving")

    window = SimpleNamespace(close=lambda: None, focus=lambda: None, open=lambda: True)
    monkeypatch.setattr(tycho.desktop.tray, "TrayIcon", _Tray)
    monkeypatch.setattr(tycho.desktop.host, "Host", Host)

    def app_window(**_options: object) -> SimpleNamespace:
        return window

    monkeypatch.setattr(shell, "app_window", app_window)
    session = SimpleNamespace(paused=False, tell=told.append)

    async def serve(_stop: asyncio.Event | None) -> None:
        raise AssertionError("the fake host never runs it")

    outcome = shell.serve_with_tray(session, {}, None, serve=serve)  # type: ignore[arg-type]

    assert outcome == "stopped"
    assert told == [shell.TRAY_STOPPED]
    assert served == ["still serving"]
