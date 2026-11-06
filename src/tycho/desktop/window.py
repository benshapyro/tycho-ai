"""The app window (ADR 17): pywebview over Windows' own web view, on the main thread.

The page is one HTML file with its CSS and script put inline, loaded from memory: no
local server, nothing listening on a port. The page calls Python only through the
bridge (``js_api``). The window is closed, not hidden, when the player closes it, so it
uses no memory while they play; the tray icon opens it again.
"""

import importlib
import logging
import threading
from importlib import resources
from typing import Any

import httpx2

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

    def open(self) -> None:
        # Imported here: pythonnet starts .NET, which only the window needs.
        webview: Any = importlib.import_module("webview")

        with self._lock:
            self._window = webview.create_window(
                "Tycho",
                html=page(),
                js_api=self._bridge,
                width=SIZE[0],
                height=SIZE[1],
                min_size=SMALLEST,
                background_color=BACKGROUND,
            )
        try:
            webview.start(gui="edgechromium", private_mode=True)
        except Exception as e:  # no WebView2 runtime, or it failed: Tycho carries on
            log.warning("The window couldn't open (%s). Windows' web view: %s", e, WEBVIEW2)
        finally:
            with self._lock:
                self._window = None

    def close(self) -> None:
        with self._lock:
            window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                log.debug("Closing the window failed", exc_info=True)


def check_key(name: str, key: str) -> str:
    """Try a key with its provider in one free request. Empty if it works; otherwise
    what's wrong, in plain words. Never logs the key."""
    try:
        if name == "anthropic":
            response = httpx2.get(
                "https://api.anthropic.com/v1/models?limit=1",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                timeout=8.0,
            )
        elif name == "elevenlabs":
            response = httpx2.get(
                "https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key}, timeout=8.0
            )
        else:
            return "that isn't a key Tycho uses"
    except httpx2.HTTPError:
        return "Couldn't reach the provider to check the key. Check your connection and try again."
    who = "Anthropic" if name == "anthropic" else "ElevenLabs"
    if (
        name == "elevenlabs"
        and response.status_code in (401, 403)
        and "permission" in response.text
    ):
        return ""  # a key limited to speech: valid, just not allowed to read the account
    if response.status_code in (401, 403):
        return f"{who} refused that key. Copy it again, or make a new one."
    if response.status_code >= 400:
        return f"{who} answered with an error ({response.status_code}). Try again in a moment."
    return ""
