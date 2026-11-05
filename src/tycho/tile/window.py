"""The Tile's window (ADR 16): a see-through Win32 window over the game, nothing in it.

It stays on top and is see-through per pixel (``UpdateLayeredWindow`` with a
premultiplied 32-bit image). Clicks pass through it, it never takes focus (Tycho
presses keys only while the game has focus), and it has no taskbar entry. It never
touches the game: no injection, no hooks, no reading its memory (ADR 3).

The window lives on its own thread with its own message loop, as Win32 requires. A
timer asks ``frame`` for what to show about 15 times a second; ``frame`` returns the
image and where it goes, ``None`` to hide the window, or ``SAME`` to leave it as it is,
which is most ticks. That thread is DPI-aware per monitor, so its coordinates are the
screen's real pixels.
"""

import contextlib
import ctypes
import logging
import os
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final

from PIL import Image

log = logging.getLogger(__name__)

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020  # clicks go to whatever is underneath
WS_EX_TOOLWINDOW = 0x00000080  # no taskbar button, no Alt+Tab entry
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000  # never becomes the foreground window
STYLES: Final = (
    WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
)
FRAME_MS = 66
"""How often the window asks for a frame: about 15 a second, enough for a fade."""

_WM_DESTROY, _WM_CLOSE, _WM_TIMER = 0x0002, 0x0010, 0x0113
_WM_MOUSEACTIVATE, _MA_NOACTIVATE = 0x0021, 3
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 4
_HWND_TOPMOST = -1
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
_ULW_ALPHA, _AC_SRC_OVER, _AC_SRC_ALPHA = 0x2, 0x0, 0x1
_DIB_RGB_COLORS, _BI_RGB = 0, 0
_MONITOR_DEFAULTTOPRIMARY = 1
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


@dataclass(frozen=True, slots=True)
class Screen:
    """The monitor the Tile goes on, in real pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class _Same:
    """Leave the window as it is."""


SAME: Final = _Same()
type Frame = tuple[Image.Image, int, int] | _Same | None
"""What to show and where (its top-left corner on screen), or None to hide."""


def bgra_premultiplied(image: Image.Image) -> bytes:
    """``image`` as Windows wants a layered window's pixels: each colour times its
    alpha, in blue, green, red, alpha order."""
    return image.convert("RGBa").tobytes("raw", "BGRa")


class TileWindow:
    def __init__(self, frame: Callable[[Screen], Frame]) -> None:
        """``frame`` is called on the window's thread with the screen the Tile is on."""
        self._frame = frame
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._hwnd = 0
        self._shown = False
        self.failed: str = ""
        """Why the window couldn't open, if it couldn't; the Tile is then off."""

    def start(self) -> bool:
        """Open the window, hidden. False, with ``failed`` saying why, if it can't."""
        if sys.platform != "win32":
            self.failed = "the Tile needs Windows"
            return False
        self._thread = threading.Thread(target=self._run, name="tycho-tile", daemon=True)
        self._thread.start()
        self._ready.wait(5.0)
        return bool(self._hwnd) and not self.failed

    def close(self) -> None:
        if self._hwnd:
            _user32().PostMessageW(self._hwnd, _WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(2.0)

    # On the window's thread.

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as e:  # the Tile must never take Tycho down
            log.exception("The Tile's window stopped")
            self.failed = f"{type(e).__name__}: {e}"
        finally:
            self._hwnd = 0
            self._ready.set()

    def _loop(self) -> None:
        user32 = _user32()
        with contextlib.suppress(AttributeError, OSError):  # older Windows lacks it
            user32.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        previous = user32.GetForegroundWindow()

        @_WNDPROC
        def proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == _WM_TIMER:
                self._tick()
                return 0
            if msg == _WM_MOUSEACTIVATE:
                return _MA_NOACTIVATE
            if msg == _WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == _WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        instance = _kernel32().GetModuleHandleW(None)
        name = f"TychoTile{os.getpid()}"
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = proc
        wc.hInstance = instance
        wc.lpszClassName = name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise OSError(ctypes.get_last_error(), "RegisterClassExW failed")
        hwnd = user32.CreateWindowExW(
            STYLES, name, "Tycho Tile", WS_POPUP, 0, 0, 1, 1, None, None, instance, None
        )
        if not hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
        self._hwnd = hwnd
        user32.SetTimer(hwnd, 1, FRAME_MS, None)
        self._ready.set()
        _give_back(previous)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterClassW(name, instance)
        _ = proc  # kept alive until the loop ends

    def _tick(self) -> None:
        try:
            frame = self._frame(screen_of_foreground())
        except Exception:
            log.exception("Drawing the Tile failed")
            frame = None
        if isinstance(frame, _Same):
            return
        user32 = _user32()
        if frame is None:
            if self._shown:
                user32.ShowWindow(self._hwnd, _SW_HIDE)
                self._shown = False
            return
        image, x, y = frame
        self._update(image, x, y)
        if not self._shown:
            previous = user32.GetForegroundWindow()
            user32.ShowWindow(self._hwnd, _SW_SHOWNOACTIVATE)
            _give_back(previous)
            self._shown = True
        # Games in borderless mode can come to the top; stay above them.
        user32.SetWindowPos(
            self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
        )

    def _update(self, image: Image.Image, x: int, y: int) -> None:
        user32, gdi32 = _user32(), _gdi32()
        w, h = image.size
        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h  # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = _BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            memory_dc, ctypes.byref(info), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        try:
            if not bitmap or not bits.value:
                raise OSError(ctypes.get_last_error(), "CreateDIBSection failed")
            pixels = bgra_premultiplied(image)
            ctypes.memmove(bits.value, pixels, len(pixels))
            old = gdi32.SelectObject(memory_dc, bitmap)
            blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
            ok = user32.UpdateLayeredWindow(
                self._hwnd,
                screen_dc,
                ctypes.byref(wintypes.POINT(x, y)),
                ctypes.byref(wintypes.SIZE(w, h)),
                memory_dc,
                ctypes.byref(wintypes.POINT(0, 0)),
                0,
                ctypes.byref(blend),
                _ULW_ALPHA,
            )
            gdi32.SelectObject(memory_dc, old)
            if not ok:
                log.debug("UpdateLayeredWindow failed (%d)", ctypes.get_last_error())
        finally:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)


def screen_of_foreground() -> Screen:
    """The monitor the game (the window in front) is on, or the main one."""
    user32 = _user32()
    monitor = user32.MonitorFromWindow(user32.GetForegroundWindow(), _MONITOR_DEFAULTTOPRIMARY)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return Screen(0, 0, 1920, 1080)
    r = info.rcMonitor  # the whole monitor: a borderless game covers the taskbar too
    return Screen(r.left, r.top, r.right, r.bottom)


def _give_back(previous: int) -> None:
    """If this process took the foreground anyway, hand it back to who had it."""
    user32 = _user32()
    now = user32.GetForegroundWindow()
    if not previous or not now or now == previous:
        return
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(now, ctypes.byref(pid))
    if pid.value == os.getpid():
        log.debug("The Tile took the foreground; giving it back")
        user32.SetForegroundWindow(previous)


# --- Win32 definitions ------------------------------------------------------------

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_libs: dict[str, ctypes.WinDLL] = {}


def _lib(name: str) -> ctypes.WinDLL:
    """A Windows DLL, typed once on first use (so importing this never fails elsewhere)."""
    if name not in _libs:
        lib = ctypes.WinDLL(name, use_last_error=True)
        _libs[name] = lib
        if name == "user32":
            _type_user32(lib)
        elif name == "gdi32":
            _type_gdi32(lib)
        elif name == "kernel32":
            lib.GetModuleHandleW.restype = wintypes.HMODULE
            lib.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    return _libs[name]


def _user32() -> ctypes.WinDLL:
    return _lib("user32")


def _gdi32() -> ctypes.WinDLL:
    return _lib("gdi32")


def _kernel32() -> ctypes.WinDLL:
    return _lib("kernel32")


def _type_user32(u: ctypes.WinDLL) -> None:
    H, P = wintypes.HWND, ctypes.c_void_p
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.RegisterClassExW.restype = wintypes.ATOM
    u.RegisterClassExW.argtypes = [P]
    u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    u.CreateWindowExW.restype = H
    u.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        H,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    u.DestroyWindow.argtypes = [H]
    u.PostMessageW.argtypes = [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u.GetMessageW.argtypes = [P, H, wintypes.UINT, wintypes.UINT]
    u.TranslateMessage.argtypes = [P]
    u.DispatchMessageW.argtypes = [P]
    u.DispatchMessageW.restype = ctypes.c_ssize_t
    u.SetTimer.restype = ctypes.c_size_t
    u.SetTimer.argtypes = [H, ctypes.c_size_t, wintypes.UINT, P]
    u.ShowWindow.argtypes = [H, ctypes.c_int]
    u.SetWindowPos.argtypes = [
        H,
        H,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    u.GetForegroundWindow.restype = H
    u.SetForegroundWindow.argtypes = [H]
    u.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
    u.GetDC.restype = wintypes.HDC
    u.GetDC.argtypes = [H]
    u.ReleaseDC.argtypes = [H, wintypes.HDC]
    u.UpdateLayeredWindow.argtypes = [
        H,
        wintypes.HDC,
        P,
        P,
        wintypes.HDC,
        P,
        wintypes.COLORREF,
        P,
        wintypes.DWORD,
    ]
    u.MonitorFromWindow.restype = wintypes.HMONITOR
    u.MonitorFromWindow.argtypes = [H, wintypes.DWORD]
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, P]
    u.SetThreadDpiAwarenessContext.restype = P
    u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]


def _type_gdi32(g: ctypes.WinDLL) -> None:
    P = ctypes.c_void_p
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateDIBSection.restype = wintypes.HBITMAP
    g.CreateDIBSection.argtypes = [
        wintypes.HDC,
        P,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
