"""Which screen the Tile shows on (#447, ADR 16 amended): the game's, or one the player
picked. The choice and its fallback are plain functions, tested with no screen; only
``connected`` asks Windows.

A screen is saved by the name Windows gives its device ("\\\\.\\DISPLAY2"), which stays
the same while it stays plugged into the same port. Its number here is only for showing:
the main screen first, then left to right.
"""

import contextlib
import ctypes
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tycho import win32
from tycho.tile.window import Screen

log = logging.getLogger(__name__)

GAME = "game"
"""The setting's value for following the game: the screen of the window in front."""
KEEP_SECONDS = 2.0
"""How long the list of screens is kept: the Tile asks for a frame 15 times a second."""

_MONITORINFOF_PRIMARY = 1
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


@dataclass(frozen=True, slots=True)
class Connected:
    """A screen plugged in now."""

    device: str
    """Windows' name for it, as saved in the setting."""
    screen: Screen
    main: bool = False


def ordered(screens: Sequence[Connected]) -> list[Connected]:
    """The main screen first, then left to right, then top to bottom."""
    return sorted(screens, key=lambda c: (not c.main, c.screen.left, c.screen.top))


def named(screens: Sequence[Connected]) -> list[dict[str, str]]:
    """Each screen as the window lists it: "Screen 1 (main), 2560 x 1440"."""
    return [
        {
            "value": c.device,
            "name": f"Screen {n}{' (main)' if c.main else ''}, "
            f"{c.screen.width} x {c.screen.height}",
        }
        for n, c in enumerate(ordered(screens), 1)
    ]


def chosen(choice: str, screens: Sequence[Connected]) -> Screen | None:
    """The screen ``choice`` names, if it's plugged in; None to follow the game, which is
    also where a screen that isn't plugged in falls back to."""
    if choice == GAME:
        return None
    return next((c.screen for c in screens if c.device == choice), None)


class Where:
    """Where the Tile goes each frame, on the Tile's thread: the chosen screen, or the
    game's. A chosen screen that isn't plugged in is said once (``on_missing``, which
    returns whether the player was told), and said again only after it has been back.
    A list that failed means "don't know": the Tile follows the game and says nothing."""

    def __init__(
        self,
        choice: Callable[[], str],
        *,
        foreground: Callable[[], Screen],
        connected: Callable[[], list[Connected]],
        on_missing: Callable[[str], bool] = lambda _choice: True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._choice = choice
        self._foreground = foreground
        self._connected = connected
        self._on_missing = on_missing
        self._clock = clock
        self._kept: tuple[float, list[Connected] | None] | None = None
        self._missing = ""
        self._logged = ""

    def __call__(self) -> Screen:
        choice = self._choice()
        if choice == GAME:
            return self._foreground()
        plugged = self._screens()
        if plugged is None:  # don't know: not "unplugged" (#468 review)
            return self._foreground()
        screen = chosen(choice, plugged)
        if screen is not None:
            self._missing = self._logged = ""
            return screen
        if self._logged != choice:
            self._logged = choice
            log.warning("The Tile's screen %s isn't plugged in; it follows the game", choice)
        if self._missing != choice and self._on_missing(choice):
            self._missing = choice  # told: else try again next frame (Tile off, notices hidden)
        return self._foreground()

    def _screens(self) -> list[Connected] | None:
        """The screens plugged in, kept for a moment; None if listing them failed."""
        now = self._clock()
        if self._kept is None or now - self._kept[0] > KEEP_SECONDS:
            try:
                self._kept = (now, self._connected())
            except OSError, AttributeError:
                log.debug("Listing the screens failed", exc_info=True)
                self._kept = (now, None)
        return self._kept[1]


def connected() -> list[Connected]:
    """The screens plugged in now, in real pixels."""
    user32 = win32.user32()
    found: list[Connected] = []

    def each(monitor: int, _dc: int, _rect: object, _data: int) -> bool:
        info = win32.MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(win32.MONITORINFOEXW)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcMonitor  # the whole screen: a borderless game covers the taskbar
            main = bool(info.dwFlags & _MONITORINFOF_PRIMARY)
            found.append(Connected(info.szDevice, Screen(r.left, r.top, r.right, r.bottom), main))
        return True

    callback = win32.MONITORENUMPROC(each)
    # Real pixels whichever thread asks: the window's own thread isn't DPI-aware.
    before = None
    with contextlib.suppress(AttributeError, OSError):  # older Windows lacks it
        before = user32.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        if not user32.EnumDisplayMonitors(None, None, callback, 0):
            raise OSError(ctypes.get_last_error(), "EnumDisplayMonitors failed")
    finally:
        if before:
            with contextlib.suppress(Exception):
                user32.SetThreadDpiAwarenessContext(before)
    return found
