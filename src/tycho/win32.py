"""Every Windows DLL Tycho calls, loaded here and nowhere else (#298, ADR 19): the
Tile's window (ADR 16), the tray icon (ADR 17), key presses and the focus check, push
to talk, the single-instance lock, the console's close guard, and the game's drives.
One copy, so a fix to a structure or an argument type reaches every caller.

Each DLL is loaded and typed on first use. Every function a caller uses has its
``argtypes`` and ``restype`` declared, so a handle is never cut to a 32-bit int. A test
checks that no other module loads a DLL (``tests/test_code_layout.py``).
"""

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

# WINFUNCTYPE is Windows' own; the stand-in keeps this importable elsewhere (the cloud's
# tests), where nothing is called.
_FUNCTYPE = ctypes.WINFUNCTYPE if sys.platform == "win32" else ctypes.CFUNCTYPE
WNDPROC = _FUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
HANDLER_ROUTINE = _FUNCTYPE(wintypes.BOOL, wintypes.DWORD)
"""A console control handler (SetConsoleCtrlHandler)."""


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
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


class BITMAPINFOHEADER(ctypes.Structure):
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


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class MONITORINFOEXW(ctypes.Structure):
    """``MONITORINFO`` with the screen's device name ("\\\\.\\DISPLAY2", #447)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORENUMPROC = _FUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)
"""``EnumDisplayMonitors``' callback: each screen in turn (#447)."""


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


_libs: dict[str, ctypes.WinDLL] = {}


def _lib(name: str) -> ctypes.WinDLL:
    if name not in _libs:
        lib = ctypes.WinDLL(name, use_last_error=True)
        _TYPERS[name](lib)
        _libs[name] = lib
    return _libs[name]


def user32() -> ctypes.WinDLL:
    return _lib("user32")


def gdi32() -> ctypes.WinDLL:
    return _lib("gdi32")


def kernel32() -> ctypes.WinDLL:
    return _lib("kernel32")


def shell32() -> ctypes.WinDLL:
    return _lib("shell32")


def _type_user32(u: ctypes.WinDLL) -> None:
    H, P, U = wintypes.HWND, ctypes.c_void_p, wintypes.UINT
    i = ctypes.c_int
    u.DefWindowProcW.restype = ctypes.c_ssize_t
    u.DefWindowProcW.argtypes = [H, U, wintypes.WPARAM, wintypes.LPARAM]
    u.RegisterClassExW.restype = wintypes.ATOM
    u.RegisterClassExW.argtypes = [P]
    u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    u.RegisterWindowMessageW.restype = U
    u.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    u.CreateWindowExW.restype = H
    u.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        i,
        i,
        i,
        i,
        H,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    u.DestroyWindow.argtypes = [H]
    u.PostMessageW.argtypes = [H, U, wintypes.WPARAM, wintypes.LPARAM]
    u.GetMessageW.argtypes = [P, H, U, U]
    u.TranslateMessage.argtypes = [P]
    u.DispatchMessageW.argtypes = [P]
    u.DispatchMessageW.restype = ctypes.c_ssize_t
    u.SetTimer.restype = ctypes.c_size_t
    u.SetTimer.argtypes = [H, ctypes.c_size_t, U, P]
    u.ShowWindow.argtypes = [H, i]
    u.SetWindowPos.argtypes = [H, H, i, i, i, i, U]
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
    u.EnumDisplayMonitors.restype = wintypes.BOOL
    u.EnumDisplayMonitors.argtypes = [wintypes.HDC, P, MONITORENUMPROC, wintypes.LPARAM]
    u.SetThreadDpiAwarenessContext.restype = P
    u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.argtypes = [wintypes.HMENU, U, ctypes.c_size_t, wintypes.LPCWSTR]
    u.SetMenuDefaultItem.argtypes = [wintypes.HMENU, U, U]
    u.TrackPopupMenu.restype = i
    u.TrackPopupMenu.argtypes = [wintypes.HMENU, U, i, i, i, H, P]
    u.DestroyMenu.argtypes = [wintypes.HMENU]
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.CreateIconIndirect.restype = wintypes.HICON
    u.CreateIconIndirect.argtypes = [P]
    u.DestroyIcon.argtypes = [wintypes.HICON]
    # Return types for the calls above that return a BOOL (or an int), and argument
    # types for those that take none, so each is declared in full (#298).
    B = wintypes.BOOL
    u.DestroyWindow.restype = B
    u.PostMessageW.restype = B
    u.GetMessageW.restype = B
    u.TranslateMessage.restype = B
    u.UnregisterClassW.restype = B
    u.ShowWindow.restype = B
    u.SetWindowPos.restype = B
    u.SetForegroundWindow.restype = B
    u.UpdateLayeredWindow.restype = B
    u.GetMonitorInfoW.restype = B
    u.AppendMenuW.restype = B
    u.SetMenuDefaultItem.restype = B
    u.DestroyMenu.restype = B
    u.GetCursorPos.restype = B
    u.DestroyIcon.restype = B
    u.ReleaseDC.restype = i
    u.GetForegroundWindow.argtypes = []
    u.CreatePopupMenu.argtypes = []
    u.PostQuitMessage.restype = None
    u.PostQuitMessage.argtypes = [i]
    # Key presses and the focus check (input/windows.py).
    u.SendInput.restype = U
    u.SendInput.argtypes = [U, P, i]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    # Push to talk (ptt/windows.py).
    u.GetAsyncKeyState.restype = wintypes.SHORT
    u.GetAsyncKeyState.argtypes = [i]
    u.MapVirtualKeyW.restype = U
    u.MapVirtualKeyW.argtypes = [U, U]
    u.GetKeyNameTextW.restype = i
    u.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, i]
    # A second start hands the foreground on (runtime/instance.py), and says so (app.py).
    u.AllowSetForegroundWindow.restype = wintypes.BOOL
    u.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    u.MessageBoxW.restype = i
    u.MessageBoxW.argtypes = [H, wintypes.LPCWSTR, wintypes.LPCWSTR, U]


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
    g.CreateBitmap.restype = wintypes.HBITMAP
    g.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, P]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.DeleteObject.restype = wintypes.BOOL
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.restype = wintypes.BOOL
    g.DeleteDC.argtypes = [wintypes.HDC]


def _type_kernel32(k: ctypes.WinDLL) -> None:
    B, D, H, S = wintypes.BOOL, wintypes.DWORD, wintypes.HANDLE, wintypes.LPCWSTR
    k.GetModuleHandleW.restype = wintypes.HMODULE
    k.GetModuleHandleW.argtypes = [S]
    k.CloseHandle.restype = B
    k.CloseHandle.argtypes = [H]
    # The focus check: which program is in front (input/windows.py).
    k.OpenProcess.restype = H
    k.OpenProcess.argtypes = [D, B, D]
    k.QueryFullProcessImageNameW.restype = B
    k.QueryFullProcessImageNameW.argtypes = [H, D, wintypes.LPWSTR, ctypes.POINTER(D)]
    # One Tycho at a time, and "show your window" (runtime/instance.py).
    k.CreateMutexW.restype = H
    k.CreateMutexW.argtypes = [wintypes.LPVOID, B, S]
    k.CreateEventW.restype = H
    k.CreateEventW.argtypes = [wintypes.LPVOID, B, B, S]
    k.OpenEventW.restype = H
    k.OpenEventW.argtypes = [D, B, S]
    k.SetEvent.restype = B
    k.SetEvent.argtypes = [H]
    k.WaitForMultipleObjects.restype = D
    k.WaitForMultipleObjects.argtypes = [D, ctypes.POINTER(H), B, D]
    # Closing the console window stops Tycho safely (runtime/console.py).
    k.SetConsoleCtrlHandler.restype = B
    k.SetConsoleCtrlHandler.argtypes = [HANDLER_ROUTINE, B]
    # The game's drives (game/install.py).
    k.GetLogicalDrives.restype = D
    k.GetLogicalDrives.argtypes = []
    k.GetDriveTypeW.restype = wintypes.UINT
    k.GetDriveTypeW.argtypes = [S]


def _type_shell32(s: ctypes.WinDLL) -> None:
    s.Shell_NotifyIconW.restype = wintypes.BOOL
    s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]


_TYPERS: dict[str, Callable[[ctypes.WinDLL], None]] = {
    "user32": _type_user32,
    "gdi32": _type_gdi32,
    "kernel32": _type_kernel32,
    "shell32": _type_shell32,
}


def message_box(text: str, title: str, flags: int) -> int:
    """A Windows message box; its answer (IDOK and so on)."""
    return int(user32().MessageBoxW(None, text, title, flags))
