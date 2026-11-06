"""Tycho's tray icon (ADR 17): an icon by the clock with a menu, drawn with Win32.

It lives on its own thread with its own message loop, like the Tile's window. The menu
is asked for each time it opens (``menu``), so it always shows the current state
("Pause listening" or "Resume listening"). A choice is passed to ``chosen`` on the
tray's thread; it must return at once.

pystray would do this, but it's LGPL (ADR 17), so it's written here with ctypes.
If Explorer restarts, the icon is added again.
"""

import ctypes
import itertools
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageDraw

from tycho import win32

log = logging.getLogger(__name__)

_WM_DESTROY, _WM_CLOSE, _WM_COMMAND, _WM_NULL = 0x0002, 0x0010, 0x0111, 0x0000
_WM_LBUTTONUP, _WM_RBUTTONUP = 0x0202, 0x0205
_WM_APP_TRAY = 0x8000 + 1
_NIM_ADD, _NIM_MODIFY, _NIM_DELETE = 0, 1, 2
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP = 0x1, 0x2, 0x4
_MF_STRING, _MF_SEPARATOR, _MF_CHECKED, _MF_GRAYED = 0x0, 0x800, 0x8, 0x1
_TPM_RIGHTBUTTON, _TPM_RETURNCMD, _TPM_NONOTIFY = 0x2, 0x100, 0x80
_ICON = 32
START_SECONDS = 15.0
"""How long Tycho waits for the tray before carrying on without it."""


@dataclass(frozen=True, slots=True)
class Item:
    """One line of the menu. An empty ``key`` is a separator."""

    key: str = ""
    label: str = ""
    checked: bool = False
    enabled: bool = True
    default: bool = False
    """Shown in bold; a click on the icon chooses it."""


SEPARATOR = Item()


def glyph(colour: tuple[int, int, int] = (0x9F, 0xD8, 0xFF), size: int = _ICON) -> Image.Image:
    """Tycho's star, for the icon: drawn at 4x and scaled down for smooth edges."""
    big = size * 4
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    k = big / 24
    star = [
        (12, 1),
        (13.9, 10.1),
        (23, 12),
        (13.9, 13.9),
        (12, 23),
        (10.1, 13.9),
        (1, 12),
        (10.1, 10.1),
    ]
    d.polygon([(x * k, y * k) for x, y in star], fill=(*colour, 255))
    r = 6.4 * k
    d.ellipse(
        (12 * k - r, 12 * k - r, 12 * k + r, 12 * k + r),
        outline=(*colour, 200),
        width=round(1.6 * k),
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


_ICONS = itertools.count()
"""Numbers the icons made in this process."""


class TrayIcon:
    def __init__(
        self,
        tip: Callable[[], str],
        menu: Callable[[], Sequence[Item]],
        chosen: Callable[[str], None],
    ) -> None:
        self._tip = tip
        self._menu = menu
        self._chosen = chosen
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._hwnd = 0
        self._icon = 0
        self._abandoned = False
        self.failed = ""

    def start(self) -> bool:
        """Show the icon. False, with ``failed`` saying why, if it can't."""
        if sys.platform != "win32":
            self.failed = "the tray icon needs Windows"
            return False
        self._thread = threading.Thread(target=self._run, name="tycho-tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(START_SECONDS):
            # Too slow: Tycho carries on without a tray, so this one must never appear.
            self._abandoned = True
            self.failed = f"the tray took more than {START_SECONDS:g} s to start"
            self.close()
            return False
        return bool(self._hwnd) and not self.failed

    def refresh(self) -> None:
        """Update the tooltip, e.g. after pausing. Safe from any thread."""
        if self._hwnd:
            win32.user32().PostMessageW(self._hwnd, _WM_APP_TRAY, 0, 1)  # lParam 1: refresh

    def close(self) -> None:
        if self._hwnd:
            win32.user32().PostMessageW(self._hwnd, _WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(2.0)

    # On the tray's thread.

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as e:  # the icon must never take Tycho down
            log.exception("The tray icon stopped")
            self.failed = f"{type(e).__name__}: {e}"
        finally:
            self._hwnd = 0
            self._ready.set()

    def _loop(self) -> None:
        user32 = win32.user32()
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        @win32.WNDPROC
        def proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == _WM_APP_TRAY:
                if lparam == 1:
                    self._notify(_NIM_MODIFY)
                elif lparam == _WM_RBUTTONUP:
                    self._popup()
                elif lparam == _WM_LBUTTONUP:
                    # A click does the default (open Tycho); with none, it shows the menu.
                    default = next((i for i in self._menu() if i.default and i.enabled), None)
                    if default is None:
                        self._popup()
                    else:
                        self._choose(default.key)
                return 0
            if msg == taskbar_created and taskbar_created:
                self._notify(_NIM_ADD)  # Explorer restarted: the icon was lost
                return 0
            if msg == _WM_CLOSE:
                self._notify(_NIM_DELETE)
                user32.DestroyWindow(hwnd)
                return 0
            if msg == _WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        instance = win32.kernel32().GetModuleHandleW(None)
        # One name per icon: the starting star and the real one may overlap briefly.
        name = f"TychoTray{os.getpid()}-{next(_ICONS)}"
        wc = win32.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(win32.WNDCLASSEXW)
        wc.lpfnWndProc = proc
        wc.hInstance = instance
        wc.lpszClassName = name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "RegisterClassExW failed")
        # A real (never shown) window, not a message-only one: only real windows
        # hear "TaskbarCreated" when Explorer restarts.
        hwnd = user32.CreateWindowExW(0, name, "Tycho", 0, 0, 0, 0, 0, None, None, instance, None)
        if not hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
        if self._abandoned:  # start() gave up waiting
            user32.DestroyWindow(hwnd)
            return
        self._hwnd = hwnd
        self._icon = _hicon(glyph())
        if not self._notify(_NIM_ADD):
            # The taskbar isn't there yet (Tycho started at login, before Explorer):
            # the icon is added when Explorer says the taskbar exists.
            log.info("The taskbar isn't ready yet; the tray icon will appear when it is")
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if self._icon:
            user32.DestroyIcon(self._icon)
        user32.UnregisterClassW(name, instance)
        _ = proc  # kept alive until the loop ends

    def _notify(self, action: int) -> bool:
        data = win32.NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(win32.NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
        data.uCallbackMessage = _WM_APP_TRAY
        data.hIcon = self._icon
        data.szTip = self._tip()[:127]
        return bool(win32.shell32().Shell_NotifyIconW(action, ctypes.byref(data)))

    def _popup(self) -> None:
        user32 = win32.user32()
        items = list(self._menu())
        menu = user32.CreatePopupMenu()
        try:
            for i, item in enumerate(items, start=1):
                if not item.key:
                    user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
                    continue
                flags = _MF_STRING | (_MF_CHECKED if item.checked else 0)
                flags |= 0 if item.enabled else _MF_GRAYED
                user32.AppendMenuW(menu, flags, i, item.label)
                if item.default:
                    user32.SetMenuDefaultItem(menu, i, 0)
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            # Windows needs the menu's owner in front, or the menu won't close when the
            # player clicks elsewhere. The player has left the game to click the icon.
            user32.SetForegroundWindow(self._hwnd)
            choice = int(
                user32.TrackPopupMenu(
                    menu,
                    _TPM_RIGHTBUTTON | _TPM_RETURNCMD | _TPM_NONOTIFY,
                    point.x,
                    point.y,
                    0,
                    self._hwnd,
                    None,
                )
            )
            user32.PostMessageW(self._hwnd, _WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
        if 0 < choice <= len(items) and items[choice - 1].key:
            self._choose(items[choice - 1].key)

    def _choose(self, key: str) -> None:
        try:
            self._chosen(key)
        except Exception:
            log.exception("The tray's %r failed", key)
        self._notify(_NIM_MODIFY)


def _hicon(image: Image.Image) -> int:
    """An icon handle from an RGBA image: a 32-bit colour bitmap with its alpha."""
    gdi32, user32 = win32.gdi32(), win32.user32()
    w, h = image.size
    info = win32.BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(win32.BITMAPINFOHEADER)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    screen = user32.GetDC(None)
    colour = gdi32.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    user32.ReleaseDC(None, screen)
    if not colour or not bits.value:
        raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")
    pixels = image.convert("RGBA").tobytes("raw", "BGRA")
    ctypes.memmove(bits.value, pixels, len(pixels))
    mask = gdi32.CreateBitmap(w, h, 1, 1, None)
    icon_info = win32.ICONINFO(True, 0, 0, mask, colour)
    icon = user32.CreateIconIndirect(ctypes.byref(icon_info))
    gdi32.DeleteObject(mask)
    gdi32.DeleteObject(colour)
    return int(icon or 0)
