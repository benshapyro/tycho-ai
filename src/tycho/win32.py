"""The Win32 definitions Tycho's own windows share: the Tile's (ADR 16) and the tray
icon's (ADR 17). One copy, so a fix to a structure or an argument type reaches both.

Each DLL is loaded and typed on first use, so importing this never fails off Windows.
"""

import ctypes
from collections.abc import Callable
from ctypes import wintypes

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


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
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]


def _type_kernel32(k: ctypes.WinDLL) -> None:
    k.GetModuleHandleW.restype = wintypes.HMODULE
    k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def _type_shell32(s: ctypes.WinDLL) -> None:
    s.Shell_NotifyIconW.restype = wintypes.BOOL
    s.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]


_TYPERS: dict[str, Callable[[ctypes.WinDLL], None]] = {
    "user32": _type_user32,
    "gdi32": _type_gdi32,
    "kernel32": _type_kernel32,
    "shell32": _type_shell32,
}
