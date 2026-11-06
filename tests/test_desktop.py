"""Tycho on the desktop (ADR 17): the voice loop on its own thread, and the tray icon."""

import asyncio
import sys
import threading

import pytest

from tycho.desktop.host import Host
from tycho.desktop.tray import SEPARATOR, Item, TrayIcon, glyph


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
