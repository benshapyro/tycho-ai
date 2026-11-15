"""Tycho's tray and window around a running session (ADR 17): the window and its bridge,
the star while Tycho starts, first-run setup, and the tray that runs the session.

Out of ``app.py`` (#398). It runs what it's given, so it imports nothing from
``tycho.start``.
"""

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, Literal

from tycho import crashes, doctor, paths, settings

if TYPE_CHECKING:
    from tycho.desktop.window import AppWindow
    from tycho.runtime import Session
    from tycho.runtime.instance import OpenRequests
    from tycho.spoken_settings import Live
    from tycho.tile.display import TileDisplay

log = logging.getLogger("tycho")


NO_WINDOW = (
    "Tycho's window can't open on this PC: it needs Microsoft Edge WebView2, which "
    "Windows 10 and 11 usually have. Install it from Microsoft "
    "(go.microsoft.com/fwlink/p/?LinkId=2124703), or run `tycho run` in a terminal "
    "to set up there."
)


TRAY_STOPPED = (
    "Tycho's icon by the clock stopped working. Tycho is still listening. Starting "
    "Tycho again from the Start menu opens its window."
)
"""Shown on the Tile, or said, when the tray icon stops while Tycho runs (#297)."""


NO_TRAY = (
    "Tycho couldn't put its icon by the clock ({why}), so it stopped: without it, there "
    "would be no way to close it."
)


def serve_with_tray(
    session: Session,
    live: Live,
    display: TileDisplay | None,
    requests: OpenRequests | None = None,
    *,
    serve: Callable[[asyncio.Event | None], Coroutine[None, None, None]],
    reopen: bool = False,
    windowed: bool = False,
) -> Literal["stopped", "restart", "failed"]:
    """Run the session with Tycho's tray icon (ADR 17): pause, the Tile, the logs, quit.
    Without a tray (it couldn't start), the terminal only, as before; with no terminal
    either (the Start menu's Tycho), it stops, says why and returns "failed". "restart":
    the window picked a new game (#295), so the caller starts everything again;
    ``reopen`` opens the window at once, as it was before that restart. ``serve`` runs
    the session until the event it's given is set (``tycho.start.serve``); ``windowed``:
    the Start menu's Tycho, with no terminal."""

    from tycho.desktop.host import Host
    from tycho.desktop.menu import TrayMenu
    from tycho.desktop.tray import TrayIcon

    host = Host(serve)

    def apply(name: str, value: object) -> bool:
        """A change made in the app, applied as voice applies one: on the voice loop.
        False if nothing running can apply it; it applies at the next start."""
        callback = live.get(name)
        return callback is not None and host.run_now(lambda: callback(value))

    window = app_window(
        apply=apply,
        ship_accent=display.ship_accent if display is not None else lambda: None,
        restart=host.restart,
    )
    host.on_quit = window.close
    host.window_open = lambda: window.is_open
    host.focus_window = window.focus
    menu = TrayMenu(
        session,
        host,
        display=display,
        save=settings.update,
        apply_tile=live.get("tile", lambda _on: None),
        open_logs=lambda: os.startfile(paths.logs_dir()),  # noqa: S606 - Explorer, Tycho's folder
        window=True,
    )
    tray = TrayIcon(menu.tip, menu.items, menu.chosen)
    menu.refresh = tray.refresh
    # The icon stopping later mustn't go unsaid (#297): the player hears it once, and
    # Tycho keeps listening.
    tray.on_stopped = lambda _why: session.tell(TRAY_STOPPED)
    if not tray.start():
        if windowed:
            log.error("No tray icon (%s), and no terminal: stopping", tray.failed)
            print(NO_TRAY.format(why=tray.failed), file=sys.stderr)
            return "failed"
        log.warning("No tray icon (%s); press Ctrl+C to stop", tray.failed)
        asyncio.run(serve(None))
        return "stopped"
    if requests is not None:
        requests.target = lambda: host.ask("open")
    if reopen:
        host.ask("open")  # the window asked for the restart: it's back, on the new loop
    elif _crash_reports_unasked():
        host.ask("open")  # set up before crash reports: the window asks, once (ADR 4, #442)
    try:
        host.run(window.open)
    finally:
        if requests is not None:
            requests.target = lambda: None
        tray.close()
    return "restart" if host.restarting else "stopped"


def _crash_reports_unasked() -> bool:
    """Set up before crash reports existed and never asked (ADR 4): the window opens at
    start to ask, even for a player who never opens it."""
    try:
        return settings.load().crash_reports is None
    except settings.SettingsError:
        return False


def app_window(
    *,
    apply: Callable[[str, object], bool] = lambda _name, _value: False,
    ship_accent: Callable[[], str | None] = lambda: None,
    first_run: bool = False,
    finish: Callable[[], None] = lambda: None,
    restart: Callable[[], bool] = lambda: False,
) -> AppWindow:
    """Tycho's window and its bridge, for the first run and for the tray alike."""
    import dataclasses

    from tycho.desktop.bridge import Bridge
    from tycho.desktop.keys import ProviderKeyCheck
    from tycho.desktop.setup import WindowsPC
    from tycho.desktop.window import AppWindow

    def findings() -> list[Mapping[str, str]]:
        current = settings.load()
        return [dataclasses.asdict(f) for f in doctor.checks(current, quick=True, ask=False)]

    bridge = Bridge(
        apply=apply,
        findings=findings,
        pc=WindowsPC(rescan=first_run),
        key_check=ProviderKeyCheck(),
        ship_accent=ship_accent,
        choose_folder=lambda: window.choose_folder(),
        finish=finish,
        first_run=first_run,
        restart=restart,
    )
    window = AppWindow(bridge)
    return window


class Starting:
    """The Start menu's Tycho, between setup and listening (review of #157). The speech
    model can take minutes to download, and nothing else shows: a star by the clock
    says what's happening, and a second start says Tycho is still starting."""

    def __init__(self, requests: OpenRequests | None) -> None:
        from tycho.desktop.tray import SEPARATOR, Item, TrayIcon

        self._now = "Starting"
        self._tray: TrayIcon | None = TrayIcon(
            lambda: f"Tycho: {self._now}…",
            lambda: [
                Item("starting", f"{self._now}…", enabled=False),
                SEPARATOR,
                Item("quit", "Quit Tycho"),
            ],
            self._chosen,
        )
        if not self._tray.start():
            self._tray = None
        self._requests = requests
        if requests is not None:
            requests.target = self.say

    def _chosen(self, key: str) -> None:
        """Quit while starting: the 2.5 GB first download can take minutes, and there's
        nothing else to stop it with. A half-downloaded file is left as .part and
        checked before use, so the next start carries on safely."""
        if key == "quit":
            log.info("Quit while starting")
            if sys.stdout is not None and sys.stdout.isatty():
                print("\nStopped.", flush=True)  # after the download's progress line
            crashes.stop()  # a clean stop, though nothing after this line runs (#296)
            os._exit(0)  # the main thread is inside the download, which can't be told

    def doing(self, now: str) -> None:
        self._now = now
        if self._tray is not None:
            self._tray.refresh()

    def say(self) -> None:
        """A second start, while this one starts: on the event's thread, so waiting on
        the box blocks nothing else."""
        from tycho import win32

        text = (
            f"Tycho is still starting: {self._now.lower()}. Its window opens from the "
            "star by the clock once it's listening."
        )
        # MB_ICONINFORMATION, MB_SETFOREGROUND and MB_TOPMOST: in front of the game.
        win32.message_box(text, "Tycho", 0x40 | 0x10000 | 0x40000)

    def close(self) -> None:
        if self._requests is not None and self._requests.target == self.say:
            self._requests.target = lambda: None
        if self._tray is not None:
            self._tray.close()
            self._tray = None


type FirstRun = Literal["done", "closed", "no_window"]


def first_run(requests: OpenRequests | None = None) -> FirstRun:
    """The window, on setup, before Tycho starts listening (ADR 17): "done" once the
    player finishes it, "closed" if they close it first, "no_window" if it can't open.
    Starting Tycho again meanwhile brings this window forward."""
    import threading

    finished = threading.Event()
    opened: list[AppWindow] = []

    def finish() -> None:
        finished.set()
        # A moment for the page to say "Starting Tycho…" before it goes.
        threading.Timer(0.8, opened[0].close).start()

    window = app_window(first_run=True, finish=finish)
    opened.append(window)
    if requests is not None:
        requests.target = window.focus
    try:
        with _ctrl_c(window.close):  # the main thread is inside the window: Ctrl+C closes it
            if not window.open():
                return "no_window"
    finally:
        if requests is not None:
            requests.target = lambda: None
    return "done" if finished.is_set() else "closed"


def _ctrl_c(on_ctrl_c: Callable[[], None]) -> contextlib.AbstractContextManager[object]:
    if sys.platform != "win32":
        return contextlib.nullcontext()
    from tycho.runtime.console import CtrlCGuard

    return CtrlCGuard(on_ctrl_c)
