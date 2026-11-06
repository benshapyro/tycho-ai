"""The Tile on screen (ADR 16): the board, the player's Tile settings and the window.

Satisfies ``runtime.Display``. The session calls it on the event loop; the window
asks it for frames on its own thread; a lock keeps the two apart. Every call returns
at once: nothing here waits on the screen.

A frame is drawn only when something changed or something moves (a timer bar, the
countdown, the talk light). With nothing to show, the window hides: the Tile is
invisible, not an empty box.
"""

import logging
import threading
import time
from collections.abc import Callable

from tycho.settings import Settings
from tycho.tile.board import Board
from tycho.tile.cards import ALL_KINDS, Card, Kind, confirm, notice
from tycho.tile.design import PICKS, TEXT_SIZES, TYCHO_ACCENT
from tycho.tile.draw import Light, Look, draw
from tycho.tile.window import SAME, Frame, Screen, TileWindow

log = logging.getLogger(__name__)

MARGIN = 0.022
"""The gap between the Tile and the screen's edge, as a share of the screen's width."""
STAY: dict[str, float] = {"shorter": 0.6, "normal": 1.0, "longer": 1.6}
SAMPLE_SECONDS = 3.0
"""How long the sample card shows after a Tile setting changes."""
PERIOD = {"listening": 1.0, "thinking": 1.1}
"""Seconds per pulse of the talk light."""
ACCENT_SECONDS = 1.0
"""How often to ask which ship the player is in: the answer needs a name match."""


class TileDisplay:
    def __init__(
        self,
        current: Settings,
        *,
        ship_accent: Callable[[], str | None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        window: Callable[[Callable[[Screen], Frame]], TileWindow] = TileWindow,
    ) -> None:
        """``ship_accent`` is the accent of the ship the player is flying, if known."""
        self._settings = current
        self.ship_accent = ship_accent
        """Set once the ship data is loaded (``app.load_brain``)."""
        self._clock = clock
        self._lock = threading.Lock()
        self._board = Board(clock=clock)
        self._apply()
        self._light: Light = None
        self._light_since = 0.0
        self._changed = True
        self._accent = TYCHO_ACCENT
        self._accent_at = -ACCENT_SECONDS
        self._last: tuple[int, int, int, int] | None = None
        self._window = window(self.frame)

    def start(self) -> bool:
        """Open the window. False if it can't, and the Tile stays off."""
        if self._window.start():
            return True
        log.warning("The Tile is off: %s", self._window.failed)
        return False

    def close(self) -> None:
        self._window.close()

    @property
    def on(self) -> bool:
        return self._settings.tile

    def allows(self, kind: Kind) -> bool:
        with self._lock:
            return self.on and self._board.allows(kind)

    # What the session tells it.

    def listening(self) -> None:
        self._set_light("listening")

    def thinking(self) -> None:
        self._set_light("thinking")

    def idle(self) -> None:
        self._set_light(None)

    def show(self, card: Card, *, pinned: bool = False) -> bool:
        """Put a card up. False if the Tile is off or the player hid its kind."""
        if not self.on:
            return False
        with self._lock:
            shown = self._board.show(card, pinned=pinned)
            self._changed = True
        return shown

    def ask(self, question: str, seconds: float) -> None:
        """The safety gate's question, with its countdown. Shown whatever the settings,
        while the Tile is on."""
        self.show(confirm(question, seconds))

    def answered(self) -> None:
        with self._lock:
            self._board.drop("confirm")
            self._changed = True

    def pin(self) -> bool:
        with self._lock:
            self._changed = True
            return self.on and self._board.pin()

    def again(self) -> bool:
        with self._lock:
            self._changed = True
            return self.on and self._board.again()

    def clear(self) -> None:
        with self._lock:
            self._board.clear()
            self._changed = True

    def change(self, name: str, value: object) -> None:
        """A Tile setting changed by voice (#111): apply it, and show a sample card for a
        few seconds so the player sees it over their own cockpit."""
        with self._lock:
            self._settings = self._settings.model_copy(update={name: value})
            self._apply()
            self._changed = True
        if name != "tile" and self.on:
            words = name.removeprefix("tile_").replace("_", " ")
            shown = f"{value:.0%}" if isinstance(value, float) else str(value)
            if isinstance(value, bool):
                shown = "on" if value else "off"
            with self._lock:
                self._board.show(
                    notice(f"The Tile's {words}: {shown}. Say undo to put it back."),
                    seconds=SAMPLE_SECONDS,
                )

    # On the window's thread.

    def frame(self, screen: Screen) -> Frame:
        """What the window shows now: drawn when something changed or moves."""
        now = self._clock()
        with self._lock:
            if not self.on:
                self._last = None
                return None
            view = self._board.view()
            light = self._light if self._settings.tile_talk_light else None
            if not view and light is None:
                self._last = None
                return None
            moving = light is not None or any(s.until is not None for s in view)
            accent = self._accent_now(now)
            look = self._look(screen, accent)
            if not (moving or self._changed or self._last is None):
                return SAME
            self._changed = False
            phase = 0.0
            if light is not None:
                phase = ((now - self._light_since) % PERIOD[light]) / PERIOD[light]
            image = draw(view, look, now=now, light=light, phase=phase)
        x, y = self._place(screen, image.width, image.height)
        self._last = (x, y, image.width, image.height)
        return image, x, y

    # Inside the lock.

    def _set_light(self, light: Light) -> None:
        with self._lock:
            if light != self._light:
                self._light, self._light_since = light, self._clock()
                self._changed = True

    def _apply(self) -> None:
        s = self._settings
        self._board.at_once = 2 if s.tile_cards_at_once == "two" else 1
        self._board.stay = STAY[s.tile_cards_stay]
        self._board.quiet = s.tile_quiet
        hidden = {k.strip() for k in s.tile_hidden_cards.split(",") if k.strip()}
        kinds: frozenset[Kind] = frozenset(k for k in ALL_KINDS if k in hidden)
        self._board.hidden = kinds

    def _accent_now(self, now: float) -> str:
        colour = self._settings.tile_colour
        if colour != "ship":
            return accent(colour)
        if now - self._accent_at >= ACCENT_SECONDS:
            self._accent_at = now
            try:
                self._accent = self.ship_accent() or TYCHO_ACCENT
            except Exception:  # which ship it is mustn't stop the Tile
                log.debug("Couldn't tell the ship's colour", exc_info=True)
                self._accent = TYCHO_ACCENT
        return self._accent

    def _look(self, screen: Screen, accent: str) -> Look:
        return look(self._settings, screen, accent)

    def _place(self, screen: Screen, w: int, h: int) -> tuple[int, int]:
        return place(self._settings, screen, w, h)


# The Tile's size, place and colour from the player's settings: shared with the app
# window's preview (ADR 17), so the preview is the Tile, not a copy of it.


def accent(colour: str, ship: str | None = None) -> str:
    """The Tile's accent for its colour setting: the ship's (if known), Tycho's, or a pick."""
    if colour == "ship":
        return ship or TYCHO_ACCENT
    if colour == "tycho":
        return TYCHO_ACCENT
    return PICKS.get(colour, TYCHO_ACCENT)


def look(s: Settings, screen: Screen, accent: str) -> Look:
    """How the Tile is drawn on ``screen``: its text grows with the screen's height."""
    return Look(
        accent=accent,
        scale=TEXT_SIZES[s.tile_size] * screen.height / 1080,
        see_through=round(s.tile_see_through * 100),
        high_contrast=s.tile_high_contrast,
        left=s.tile_corner.endswith("left"),
        bottom=s.tile_corner.startswith("bottom"),
    )


def place(s: Settings, screen: Screen, w: int, h: int) -> tuple[int, int]:
    """Where a ``w`` by ``h`` Tile goes on ``screen``: its corner, nudged in and down."""
    margin = round(MARGIN * screen.width)
    across = margin + round(s.tile_nudge_in * screen.width)
    down = margin + round(s.tile_nudge_down * screen.height)
    x = screen.left + across if s.tile_corner.endswith("left") else screen.right - w - across
    y = screen.bottom - h - down if s.tile_corner.startswith("bottom") else screen.top + down
    return x, y
