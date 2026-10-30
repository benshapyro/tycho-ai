"""Windows key presses (SendInput with scan codes) and the game-focus check."""

import ctypes
import os
from ctypes import wintypes

from tycho.input.keys import Code

_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_KEYEVENTF_EXTENDEDKEY, _KEYEVENTF_KEYUP, _KEYEVENTF_SCANCODE = 0x0001, 0x0002, 0x0008
_MOUSEEVENTF_WHEEL, _WHEEL_DELTA = 0x0800, 120
# Mouse buttons 1-5 (left, right, middle, back, forward): (down flag, up flag, extra data).
_MOUSE_BUTTONS = {
    1: (0x0002, 0x0004, 0),
    2: (0x0008, 0x0010, 0),
    3: (0x0020, 0x0040, 0),
    4: (0x0080, 0x0100, 1),
    5: (0x0080, 0x0100, 2),
}
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GAME_EXECUTABLE = "StarCitizen.exe"


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD))


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT))


class _INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", _INPUTUNION))


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT


def _send(event: _INPUT) -> None:
    if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
        raise ctypes.WinError(ctypes.get_last_error())


class WindowsKeySender:
    """Sends key and mouse events with SendInput. Satisfies ``KeySender``."""

    def down(self, code: Code) -> None:
        self._button(code, up=False)

    def up(self, code: Code) -> None:
        self._button(code, up=True)

    def wheel(self, notches: int) -> None:
        event = _INPUT(type=_INPUT_MOUSE)
        event.union.mi = _MOUSEINPUT(
            mouseData=ctypes.c_uint32(notches * _WHEEL_DELTA).value, dwFlags=_MOUSEEVENTF_WHEEL
        )
        _send(event)

    def _button(self, code: Code, *, up: bool) -> None:
        if code.device == "key":
            flags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_EXTENDEDKEY if code.extended else 0)
            event = _INPUT(type=_INPUT_KEYBOARD)
            event.union.ki = _KEYBDINPUT(
                wScan=code.value, dwFlags=flags | (_KEYEVENTF_KEYUP if up else 0)
            )
        elif code.device == "mouse":
            down_flag, up_flag, data = _MOUSE_BUTTONS[code.value]
            event = _INPUT(type=_INPUT_MOUSE)
            event.union.mi = _MOUSEINPUT(mouseData=data, dwFlags=up_flag if up else down_flag)
        else:
            raise ValueError("wheel notches are sent with wheel(), not down()/up()")
        _send(event)


class WindowsFocusCheck:
    """True only while Star Citizen's window is in the foreground. Satisfies ``FocusCheck``."""

    def __init__(self, executable: str = GAME_EXECUTABLE) -> None:
        self.executable = executable.lower()

    def game_has_focus(self) -> bool:
        return foreground_executable().lower() == self.executable


def foreground_executable() -> str:
    """The file name of the program whose window is in front, or "" if unknown."""
    window = _user32.GetForegroundWindow()
    if not window:
        return ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
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
