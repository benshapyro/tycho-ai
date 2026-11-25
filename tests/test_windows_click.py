"""The route flow's click on the game window (#588): only on the window in front, at
the frame's pixel, after a small mouse move as input (Ben's PC try, 25 Nov 2026).

A fake ``user32`` stands in for Windows, so this runs anywhere.
"""

from ctypes import wintypes
from typing import cast

import pytest

from tycho import win32
from tycho.input import windows
from tycho.input.windows import WindowsClicker

WINDOW = 7


class FakeUser32:
    def __init__(self, front: int = WINDOW, size: tuple[int, int] = (5120, 2160)) -> None:
        self.front = front
        self.size = size
        self.done: list[tuple[object, ...]] = []

    def SetThreadDpiAwarenessContext(self, context: int) -> int:
        return 1

    def GetForegroundWindow(self) -> int:
        return self.front

    def GetWindowRect(self, window: int, rect: object) -> int:
        found = cast(wintypes.RECT, getattr(rect, "_obj"))  # noqa: B009 - ctypes.byref's target
        found.left, found.top = 100, 50
        found.right, found.bottom = 100 + self.size[0], 50 + self.size[1]
        return 1

    def SetCursorPos(self, x: int, y: int) -> int:
        self.done.append(("pointer", x, y))
        return 1

    def SendInput(self, count: int, event: object, size: int) -> int:
        sent = cast(windows._INPUT, getattr(event, "_obj"))  # noqa: B009  # pyright: ignore[reportPrivateUsage]
        mouse = cast(windows._MOUSEINPUT, sent.union.mi)  # pyright: ignore[reportPrivateUsage]
        flags, dx, dy = int(mouse.dwFlags), int(mouse.dx), int(mouse.dy)
        self.done.append(("move", dx, dy, flags) if flags & 0x0001 else ("button", flags))
        return count


@pytest.fixture
def user32(monkeypatch: pytest.MonkeyPatch) -> FakeUser32:
    fake = FakeUser32()
    monkeypatch.setattr(win32, "user32", lambda: fake)
    return fake


def test_the_pointer_moves_as_input_by_a_pixel_and_back_before_the_click(
    user32: FakeUser32,
) -> None:
    waits: list[float] = []
    assert WindowsClicker(sleep=waits.append).click(WINDOW, (5120, 2160), 1219, 277) == ""
    assert user32.done == [
        ("pointer", 100 + 1219, 50 + 277),
        ("move", 1, 0, 0x0001),  # relative: never an absolute move a game may steer by
        ("move", -1, 0, 0x0001),
        ("button", 0x0002),
        ("button", 0x0004),
    ]
    assert waits == [windows.MOVE_SECONDS, windows.CLICK_SECONDS]


def test_nothing_moves_when_the_game_isnt_in_front(user32: FakeUser32) -> None:
    user32.front = 99
    said = WindowsClicker(sleep=lambda _s: None).click(WINDOW, (5120, 2160), 10, 10)
    assert said == "the game window isn't in front"
    assert user32.done == []


def test_nothing_moves_when_the_window_isnt_the_frames_size(user32: FakeUser32) -> None:
    said = WindowsClicker(sleep=lambda _s: None).click(WINDOW, (2560, 1440), 10, 10)
    assert said.startswith("the game window is 5120x2160")
    assert user32.done == []
