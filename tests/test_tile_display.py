"""The Tile on screen (ADR 16): frames, placement and settings, with a fake window; and
the real window's styles, on Windows."""

import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from tycho.settings import Settings
from tycho.tile import Card, Row
from tycho.tile.design import PICKS, TYCHO_ACCENT
from tycho.tile.display import TileDisplay
from tycho.tile.window import SAME, STYLES, Frame, Screen, TileWindow, bgra_premultiplied

SCREEN = Screen(0, 0, 1920, 1080)
SELL = Card("list", title="Best places to sell", rows=(Row("Everus Harbor", value="3,048"),))


class FakeWindow:
    def __init__(self, frame: Callable[[Screen], Frame]) -> None:
        self.frame = frame
        self.failed = ""

    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _display(**settings: Any) -> tuple[TileDisplay, Clock]:
    clock = Clock()
    display = TileDisplay(Settings(**settings), clock=clock, window=FakeWindow)  # type: ignore[arg-type]
    return display, clock


def _image(frame: Frame) -> tuple[Any, int, int]:
    assert frame is not None
    assert frame is not SAME
    return frame  # type: ignore[return-value]


def test_with_nothing_to_show_the_window_hides() -> None:
    display, _ = _display()
    assert display.frame(SCREEN) is None


def test_a_card_is_drawn_in_the_top_right_corner() -> None:
    display, _ = _display()
    assert display.show(SELL)
    image, x, y = _image(display.frame(SCREEN))
    assert x + image.width == 1920 - round(0.022 * 1920)
    assert y == round(0.022 * 1920)


@pytest.mark.parametrize(
    ("corner", "left", "bottom"),
    [("top left", True, False), ("bottom right", False, True), ("bottom left", True, True)],
)
def test_the_player_picks_the_corner(corner: str, left: bool, bottom: bool) -> None:
    display, _ = _display(tile_corner=corner)
    display.show(SELL)
    image, x, y = _image(display.frame(SCREEN))
    assert (x < 960) is left
    assert (y + image.height > 540) is bottom


def test_a_nudge_moves_it_in_and_down() -> None:
    display, _ = _display(tile_nudge_in=0.1, tile_nudge_down=0.2)
    display.show(SELL)
    image, x, y = _image(display.frame(SCREEN))
    assert x + image.width == 1920 - round(0.022 * 1920) - 192
    assert y == round(0.022 * 1920) + 216


def test_the_tile_grows_with_the_screen_and_the_text_size() -> None:
    display, _ = _display(tile_size="large")
    display.show(SELL)
    image, *_ = _image(display.frame(Screen(0, 0, 2560, 1440)))
    assert image.width == round(400 * 1.2 * 1440 / 1080)


def test_a_still_card_isnt_drawn_again_but_a_fading_one_is() -> None:
    display, _ = _display()
    display.show(SELL, pinned=True)
    _image(display.frame(SCREEN))
    assert display.frame(SCREEN) is SAME
    display.show(Card("notice", body=("Fading.",)))
    _image(display.frame(SCREEN))
    _image(display.frame(SCREEN))  # its timer bar moves


def test_cards_fade_away_and_the_window_hides() -> None:
    display, clock = _display()
    display.show(SELL)
    _image(display.frame(SCREEN))
    clock.now += 60
    assert display.frame(SCREEN) is None


def test_the_talk_light_alone_shows_the_window_unless_turned_off() -> None:
    display, _ = _display()
    display.listening()
    _image(display.frame(SCREEN))
    display.idle()
    assert display.frame(SCREEN) is None
    quiet, _ = _display(tile_talk_light=False)
    quiet.listening()
    assert quiet.frame(SCREEN) is None


def test_with_the_tile_off_nothing_shows() -> None:
    display, _ = _display(tile=False)
    assert not display.show(SELL)
    display.ask("Eject? Say confirm.", 5.0)
    display.listening()
    assert display.frame(SCREEN) is None


def test_the_question_shows_even_in_quiet_mode_and_goes_when_answered() -> None:
    display, _ = _display(tile_quiet=True, tile_hide="list, confirm")
    assert not display.show(SELL)
    display.ask("Eject? Say confirm.", 5.0)
    _image(display.frame(SCREEN))
    display.answered()
    assert display.frame(SCREEN) is None


def test_a_change_by_voice_applies_at_once_with_a_sample_card() -> None:
    display, _ = _display()
    display.change("tile_corner", "top left")
    image, x, _ = _image(display.frame(SCREEN))
    assert x < 960
    assert image.height > 60  # the sample card
    display.change("tile", False)
    assert display.frame(SCREEN) is None
    assert not display.on


def test_the_colour_follows_the_ship_unless_the_player_picks_one() -> None:
    display, clock = _display()
    display.ship_accent = lambda: "#f5901e"
    assert display._accent_now(clock.now) == "#f5901e"  # pyright: ignore[reportPrivateUsage]
    picked, _ = _display(tile_colour="green")
    picked.ship_accent = lambda: "#f5901e"
    assert picked._accent_now(clock.now) == PICKS["green"]  # pyright: ignore[reportPrivateUsage]
    own, _ = _display(tile_colour="tycho")
    assert own._accent_now(clock.now) == TYCHO_ACCENT  # pyright: ignore[reportPrivateUsage]


def test_a_broken_ship_lookup_falls_back_to_tychos_colour() -> None:
    display, clock = _display()

    def broken() -> str:
        raise RuntimeError("no ship data")

    display.ship_accent = broken
    assert display._accent_now(clock.now) == TYCHO_ACCENT  # pyright: ignore[reportPrivateUsage]


def test_pixels_go_to_windows_premultiplied_blue_first() -> None:
    from PIL import Image

    pixel = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
    assert list(bgra_premultiplied(pixel)) == [25, 50, 100, 128]


# --- The real window, on Windows ---------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="a Win32 window")
def test_the_window_is_see_through_click_through_and_never_takes_focus() -> None:
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    asked = threading.Event()

    def frame(screen: Screen) -> Frame:
        asked.set()
        return Image.new("RGBA", (40, 20), (8, 14, 24, 200)), screen.right - 60, screen.top + 20

    before = user32.GetForegroundWindow()
    window = TileWindow(frame)
    if not window.start():
        pytest.skip(f"no desktop to open a window on: {window.failed}")
    try:
        assert asked.wait(2.0)
        time.sleep(0.2)
        style = user32.GetWindowLongPtrW(window._hwnd, -20)  # pyright: ignore[reportPrivateUsage]
        assert style & STYLES == STYLES
        assert user32.GetForegroundWindow() == before
    finally:
        window.close()
