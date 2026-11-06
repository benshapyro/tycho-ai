"""Tycho on the desktop (ADR 17): the voice loop on its own thread, and the tray icon."""

import asyncio
import sys
import threading
from collections.abc import Callable, Mapping

import pytest

from tycho.desktop.host import Host
from tycho.desktop.menu import TrayMenu
from tycho.desktop.tray import SEPARATOR, Item, TrayIcon, glyph
from tycho.settings import SettingsError


class Voice:
    """A voice loop that records how it ran and stops when told."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.thread = ""
        self.stopped = False
        self.calls: list[str] = []

    async def __call__(self, stop: asyncio.Event) -> None:
        self.thread = threading.current_thread().name
        if self.fail is not None:
            raise self.fail
        await stop.wait()
        self.stopped = True


def test_the_voice_loop_runs_on_its_own_thread_until_quit() -> None:
    voice = Voice()
    host = Host(voice)
    host.ask("quit")
    host.run()
    assert voice.thread == "tycho-voice"
    assert voice.stopped


def test_open_asks_the_main_thread_for_the_window() -> None:
    voice = Voice()
    host = Host(voice)
    opened: list[str] = []

    def open_window() -> None:
        opened.append(threading.current_thread().name)
        host.ask("quit")

    host.ask("open")
    host.run(open_window)
    assert opened == [threading.current_thread().name]
    assert host.windows == 1


def test_a_call_runs_on_the_voice_loops_thread() -> None:
    seen: list[str] = []
    started = threading.Event()

    async def voice(stop: asyncio.Event) -> None:
        started.set()
        await stop.wait()

    host = Host(voice)
    runner = threading.Thread(target=host.run, daemon=True)
    runner.start()
    assert started.wait(5)
    host.call(lambda: seen.append(threading.current_thread().name))
    host.ask("quit")
    runner.join(5)
    assert seen == ["tycho-voice"]


def test_what_ended_the_voice_loop_is_raised_on_the_main_thread() -> None:
    host = Host(Voice(fail=RuntimeError("the microphone went away")))
    with pytest.raises(RuntimeError, match="microphone"):
        host.run()


def test_ctrl_c_stops_the_voice_loop_the_same_way() -> None:
    voice = Voice()
    host = Host(voice)

    def interrupted() -> None:
        raise KeyboardInterrupt

    host.ask("open")
    with pytest.raises(KeyboardInterrupt):
        host.run(interrupted)
    assert voice.stopped


def test_the_icon_is_tychos_star_on_a_clear_background() -> None:
    icon = glyph()
    assert icon.size == (32, 32)
    assert icon.getpixel((0, 0)) == (0, 0, 0, 0)
    alpha = icon.getchannel("A")
    assert alpha.getextrema() == (0, 255)
    assert alpha.getpixel((16, 16)) == 255  # the star's centre


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows tray")
def test_the_tray_icon_shows_and_goes() -> None:
    chosen: list[str] = []
    tray = TrayIcon(
        lambda: "Tycho: listening",
        lambda: [Item("pause", "Pause listening"), SEPARATOR, Item("quit", "Quit Tycho")],
        chosen.append,
    )
    if not tray.start():
        pytest.skip(f"no taskbar here: {tray.failed}")
    try:
        tray.refresh()
        tray._choose("pause")  # pyright: ignore[reportPrivateUsage]
        assert chosen == ["pause"]
    finally:
        tray.close()


# --- The tray's menu -----------------------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.paused = False

    def pause(self, paused: bool) -> None:
        self.paused = paused


class FakeHost:
    """Runs ``call`` later, as the voice loop would, when ``run_calls`` is called."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []
        self.asked: list[str] = []

    def call(self, fn: Callable[[], None]) -> None:
        self.pending.append(fn)

    def ask(self, request: str) -> None:
        self.asked.append(request)

    def run_calls(self) -> None:
        while self.pending:
            self.pending.pop(0)()


class FakeTile:
    def __init__(self) -> None:
        self.on = True


def _menu(**options: object) -> tuple[TrayMenu, FakeSession, FakeHost]:
    session, host = FakeSession(), FakeHost()
    return TrayMenu(session, host, **options), session, host  # type: ignore[arg-type]


def test_the_menu_shows_what_pausing_would_do_and_whether_the_tile_is_on() -> None:
    tile = FakeTile()
    menu, session, _ = _menu(display=tile)
    assert [(i.key, i.label, i.checked) for i in menu.items() if i.key] == [
        ("pause", "Pause listening", False),
        ("tile", "Show the Tile", True),
        ("logs", "Open the logs folder", False),
        ("quit", "Quit Tycho", False),
    ]
    session.paused = True
    assert menu.items()[0].label == "Resume listening"
    assert menu.tip() == "Tycho: paused"


def test_with_a_window_open_tycho_comes_first_and_a_click_chooses_it() -> None:
    menu, _, host = _menu(window=True)
    first = menu.items()[0]
    assert (first.key, first.label, first.default) == ("open", "Open Tycho", True)
    menu.chosen("open")
    assert host.asked == ["open"]


def test_quitting_closes_the_window_too() -> None:
    voice = Voice()
    host = Host(voice)
    closed: list[str] = []
    host.on_quit = lambda: closed.append("closed")

    def window_open() -> None:
        host.ask("quit")  # from the tray, while the window is open

    host.ask("open")
    host.run(window_open)
    assert "closed" in closed
    assert voice.stopped


def test_without_a_tile_the_menu_has_no_tile_item() -> None:
    menu, _, _ = _menu()
    assert "tile" not in [i.key for i in menu.items()]


def test_pausing_runs_on_the_voice_loop_and_then_redraws_the_tooltip() -> None:
    menu, session, host = _menu()
    tips: list[str] = []
    menu.refresh = lambda: tips.append(menu.tip())
    menu.chosen("pause")
    assert not session.paused  # not yet: it runs on the voice loop's thread
    host.run_calls()
    assert session.paused
    assert tips == ["Tycho: paused"]  # the tooltip after the pause, never before


def test_the_tile_item_saves_and_applies_the_setting() -> None:
    saved: list[Mapping[str, object]] = []
    applied: list[bool] = []
    menu, _, _ = _menu(display=FakeTile(), save=saved.append, apply_tile=applied.append)
    menu.chosen("tile")
    assert saved == [{"tile": False}]
    assert applied == [False]


def test_a_tile_setting_that_wont_save_still_applies_until_restart() -> None:
    def broken(_changes: Mapping[str, object]) -> None:
        raise SettingsError("settings.toml is read-only")

    applied: list[bool] = []
    menu, _, _ = _menu(display=FakeTile(), save=broken, apply_tile=applied.append)
    menu.chosen("tile")
    assert applied == [False]


def test_quit_and_the_logs() -> None:
    opened: list[str] = []
    menu, _, host = _menu(open_logs=lambda: opened.append("logs"))
    menu.chosen("logs")
    menu.chosen("quit")
    assert opened == ["logs"]
    assert host.asked == ["quit"]


def test_clicking_the_icon_again_brings_the_window_forward_not_another() -> None:
    voice = Voice()
    host = Host(voice)
    focused: list[str] = []
    is_open = [False]
    host.window_open = lambda: is_open[0]
    host.focus_window = lambda: focused.append("front")
    opened: list[int] = []

    def window() -> None:
        is_open[0] = True
        opened.append(1)
        host.ask("open")  # clicked again while it's open
        host.ask("open")
        is_open[0] = False
        host.ask("quit")

    host.ask("open")
    host.ask("open")  # clicked twice before it opened: still one window
    host.run(window)
    assert opened == [1]
    assert focused == ["front", "front"]


def test_a_change_waits_for_the_voice_loop_and_reports_its_failure() -> None:
    started = threading.Event()

    async def voice(stop: asyncio.Event) -> None:
        started.set()
        await stop.wait()

    host = Host(voice)
    assert host.run_now(lambda: None) is False  # not running yet
    runner = threading.Thread(target=host.run, daemon=True)
    runner.start()
    assert started.wait(5)
    seen: list[str] = []
    assert host.run_now(lambda: seen.append(threading.current_thread().name)) is True
    assert seen == ["tycho-voice"]

    def broken() -> None:
        raise RuntimeError("couldn't")

    with pytest.raises(RuntimeError, match="couldn't"):
        host.run_now(broken)
    host.ask("quit")
    runner.join(5)


def test_no_window_opens_once_tycho_is_quitting() -> None:
    from tycho.desktop.window import AppWindow

    window = AppWindow(object())
    window.close()  # quit came first
    window.open()  # then the queued open: returns at once, no window
    assert not window.is_open


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows console")
def test_ctrl_c_reaches_tycho_even_while_the_window_is_open() -> None:
    from tycho.runtime.console import CTRL_BREAK, CTRL_C, CtrlCGuard

    quits: list[str] = []
    guard = CtrlCGuard(lambda: quits.append("quit"))
    assert guard.on_event(CTRL_C) is False  # Python still gets it too
    assert guard.on_event(CTRL_BREAK) is False
    assert quits == ["quit"]
