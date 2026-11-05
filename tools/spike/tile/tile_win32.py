"""The Win32 calls the Tile test needs: window styles, z-order, focus, memory.

Kept apart from the engines so both use the same, checked, definitions.
"""

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020  # clicks go to whatever is underneath
WS_EX_TOOLWINDOW = 0x00000080  # no taskbar button, no Alt+Tab entry
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000  # never becomes the foreground window
TILE_STYLES = (
    WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
)

HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
GW_HWNDPREV = 3
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_TH32CS_SNAPPROCESS = 0x00000002

_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE


class _MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def overlaps(self, other: Rect) -> bool:
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )

    @property
    def centre(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2


def make_tile(hwnd: int) -> None:
    """Give a window the Tile's styles and put it on top, without activating it."""
    style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | TILE_STYLES)
    raise_to_top(hwnd)


_user32.SetForegroundWindow.argtypes = [wintypes.HWND]


def give_back_focus(previous: int) -> bool:
    """If this process took the foreground, return it to the window that had it.

    A program started from the foreground window may take the foreground once, and a
    toolkit's first (even hidden) window does. Taking it is also what allows giving it back.
    """
    now = foreground()
    if not previous or not now or window_pid(now) != os.getpid():
        return False
    return bool(_user32.SetForegroundWindow(previous))


def raise_to_top(hwnd: int) -> None:
    _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def ex_style(hwnd: int) -> int:
    return int(_user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))


def foreground() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    return bool(_user32.IsWindow(hwnd))


def rect(hwnd: int) -> Rect:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect(r.left, r.top, r.right, r.bottom)


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_at(x: int, y: int) -> int:
    """The top-level window a click at (x, y) would reach. Click-through windows are skipped."""
    child = _user32.WindowFromPoint(wintypes.POINT(x, y))
    return int(_user32.GetAncestor(child, 2) or 0) if child else 0  # 2 = GA_ROOT


_dwmapi = ctypes.WinDLL("dwmapi")
_DWMWA_CLOAKED = 14


def _cloaked(hwnd: int) -> bool:
    """Windows hides some app windows by "cloaking" them, while they still report visible."""
    value = wintypes.DWORD()
    result = _dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), _DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
    )
    return result == 0 and value.value != 0


def windows_above(hwnd: int, area: Rect) -> list[int]:
    """Shown windows above this one in the z-order that overlap the area."""
    found: list[int] = []
    current = _user32.GetWindow(hwnd, GW_HWNDPREV)
    while current:
        shown = _user32.IsWindowVisible(current) and not _cloaked(int(current))
        if shown and rect(int(current)).overlaps(area):
            found.append(int(current))
        current = _user32.GetWindow(current, GW_HWNDPREV)
    return found


def executable(pid: int) -> str:
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return ""
        return os.path.basename(path.value)
    finally:
        _kernel32.CloseHandle(process)


def processes() -> list[tuple[int, int, str]]:
    """(pid, parent pid, executable) for every running process."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    found: list[tuple[int, int, str]] = []
    try:
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return found


def tree(pid: int) -> list[int]:
    """A process and all its descendants: Qt WebEngine draws in child processes."""
    children: dict[int, list[int]] = {}
    for child, parent, _ in processes():
        children.setdefault(parent, []).append(child)
    out, todo = [pid], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            out.append(child)
            todo.append(child)
    return out


def working_set_mb(pid: int) -> float:
    process = _kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not process:
        return 0.0
    try:
        counters = _MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_MEMORY_COUNTERS)
        if not _psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.WorkingSetSize / 1_048_576
    finally:
        _kernel32.CloseHandle(process)


def running(name: str) -> bool:
    return any(exe.lower() == name.lower() for _, _, exe in processes())
