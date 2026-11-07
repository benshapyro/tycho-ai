"""The app window (ADR 17): pywebview over Windows' own web view, on the main thread.

The page is one HTML file with its CSS and script put inline, loaded from memory: no
local server, nothing listening on a port. The page calls Python only through the
bridge (``js_api``). The window is closed, not hidden, when the player closes it, so it
uses no memory while they play; the tray icon opens it again.
"""

import importlib
import logging
import threading
from collections.abc import Callable
from importlib import resources
from typing import Any

log = logging.getLogger(__name__)

SIZE = (1120, 800)
SMALLEST = (760, 560)
BACKGROUND = "#070a10"
WEBVIEW2 = "https://developer.microsoft.com/microsoft-edge/webview2/"
"""Where to get Windows' web view, for a Windows 10 PC without it."""


def page() -> str:
    """The page, with its CSS and script inline."""
    folder = resources.files("tycho.desktop").joinpath("app")
    html = folder.joinpath("index.html").read_text("utf-8")
    css = folder.joinpath("app.css").read_text("utf-8")
    js = folder.joinpath("app.js").read_text("utf-8")
    return html.replace("/*CSS*/", css).replace("/*JS*/", js)


class AppWindow:
    """Opens the window on the main thread and runs it until it closes. ``close`` may be
    called from any thread, e.g. when the player quits from the tray."""

    def __init__(self, bridge: object) -> None:
        self._bridge = bridge
        self._window: Any = None
        self._lock = threading.Lock()
        self._stopping = False

    def open(self) -> bool:
        """Run the window until it closes. False if it couldn't open at all."""
        # Imported here: pythonnet starts .NET, which only the window needs.
        webview: Any = importlib.import_module("webview")

        with self._lock:
            if self._stopping:  # Tycho began quitting as the window was asked for
                return True
            self._window = webview.create_window(
                "Tycho",
                html=page(),
                js_api=self._bridge,
                width=SIZE[0],
                height=SIZE[1],
                min_size=SMALLEST,
                background_color=BACKGROUND,
            )
            # Brought to the front once shown: opened from the tray or by a second start,
            # Windows only flashed it on the taskbar (found trying the installed app).
            events = getattr(self._window, "events", None)
            if events is not None:
                events.shown += self.focus
        try:
            webview.start(gui="edgechromium", private_mode=True)
        except Exception as e:  # no WebView2 runtime, or it failed: Tycho carries on
            log.warning("The window couldn't open (%s). Windows' web view: %s", e, WEBVIEW2)
            return False
        finally:
            with self._lock:
                self._window = None
            # The page has gone: anything it was waiting on must stop, or a push-to-talk
            # capture would save the next key pressed in the game.
            cancel: Callable[[], object] | None = getattr(self._bridge, "cancel_capture", None)
            if cancel is not None:
                cancel()
        return True

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._window is not None

    def focus(self) -> None:
        """Bring the open window to the front, e.g. when the tray icon is clicked again."""
        with self._lock:
            window = self._window
        if window is not None:
            try:
                window.restore()
                window.show()
                window.on_top = True  # raised above everything once, then let go
                window.on_top = False
            except Exception:
                log.debug("Bringing the window forward failed", exc_info=True)

    def choose_folder(self) -> str | None:
        """Windows' own folder dialog, over the window; None if the player cancels."""
        with self._lock:
            window = self._window
        if window is None:
            return None
        webview: Any = importlib.import_module("webview")
        chosen = window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(chosen[0]) if chosen else None

    def close(self) -> None:
        """Tycho is quitting: close the window, and never open another."""
        with self._lock:
            self._stopping = True
            window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                log.debug("Closing the window failed", exc_info=True)
