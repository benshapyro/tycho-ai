"""Windows key presses (SendInput with scan codes), the game-focus check, and the route
flow's left click on the star map (#588)."""

import contextlib
import ctypes
import os
import time
from collections.abc import Callable
from ctypes import wintypes

from tycho import win32
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
_LEFT = 1
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
CLICK_SECONDS = 0.06
"""How long the left button is held: a plain click, like a tap's key."""
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


def _send(event: _INPUT) -> None:
    if win32.user32().SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
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


class WindowsClicker:
    """One left click on the game window, at a spot in a frame the check just read (ADR
    3's amendments for #588). Satisfies ``runtime.route.Clicker``. Blocking: call it on
    the key thread.

    It clicks only if ``window`` is the window in front and is exactly the size of the
    frame the spot came from, so the spot is the same pixel on screen. Otherwise (a
    windowed game with borders, another window in front) nothing is clicked."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    def click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str:
        """Click at (``x``, ``y``) in ``window``'s ``frame``-sized picture: "" if it
        clicked, else why not."""
        user32 = win32.user32()
        # Real pixels, as the frame has them, whatever the screen's scaling: on this
        # thread, and only for this click.
        before = None
        with contextlib.suppress(AttributeError, OSError):
            before = user32.SetThreadDpiAwarenessContext(
                _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            )
        try:
            return self._click(window, frame, x, y)
        finally:
            if before:
                with contextlib.suppress(AttributeError, OSError):
                    user32.SetThreadDpiAwarenessContext(before)

    def _click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str:
        user32 = win32.user32()
        if user32.GetForegroundWindow() != window:
            return "the game window isn't in front"
        rect = wintypes.RECT()
        if not user32.GetWindowRect(window, ctypes.byref(rect)):
            return "the game window's place on screen is unknown"
        size = (rect.right - rect.left, rect.bottom - rect.top)
        if size != frame:
            return f"the game window is {size[0]}x{size[1]}, not the frame's {frame[0]}x{frame[1]}"
        if not 0 <= x < size[0] or not 0 <= y < size[1]:
            return "the spot is outside the game window"
        if not user32.SetCursorPos(rect.left + x, rect.top + y):
            return "Windows didn't move the pointer"
        sender = WindowsKeySender()
        left = Code("mouse", _LEFT)
        sender.down(left)
        try:
            self._sleep(CLICK_SECONDS)
        finally:
            sender.up(left)
        return ""


class WindowsFocusCheck:
    """True only while Star Citizen's window is in the foreground. Satisfies ``FocusCheck``."""

    def __init__(self, executable: str = GAME_EXECUTABLE) -> None:
        self.executable = executable.lower()

    def game_has_focus(self) -> bool:
        return foreground_executable().lower() == self.executable


def foreground_executable() -> str:
    """The file name of the program whose window is in front, or "" if unknown."""
    window = win32.user32().GetForegroundWindow()
    return executable_of(window) if window else ""


def executable_of(window: int) -> str:
    """The file name of the program that owns ``window``, or "" if unknown."""
    pid = wintypes.DWORD()
    win32.user32().GetWindowThreadProcessId(window, ctypes.byref(pid))
    return executable_of_process(pid.value)


def executable_of_process(pid: int) -> str:
    """The file name of process ``pid``'s program, or "" if unknown."""
    kernel32 = win32.kernel32()
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return ""
        return os.path.basename(path.value)
    finally:
        kernel32.CloseHandle(process)
