"""What the Tile shows, and for how long (#2): the rules, with no window.

The drawing reads ``Board.view()``; everything that decides it lives here, so it is
tested without a screen:

- Cards fade by themselves, longer ones staying longer. A confirm card lasts as
  long as its question; a long answer, which the player asked to read, is pinned.
- One card at a time by default. A new card replaces the one showing, except that a
  pinned card shrinks to a strip ("show that again" brings it back). With two at
  once, the oldest gives way and there are no strips.
- The player chooses which kinds show. Confirm always does while the Tile is on;
  quiet mode leaves only confirm, warnings and notices. A hidden card is still said
  out loud: hiding it only keeps it off the screen.
"""

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace

from tycho.tile.cards import Card, Kind

ALWAYS: frozenset[Kind] = frozenset({"confirm"})
QUIET: frozenset[Kind] = frozenset({"confirm", "warning", "notice"})
STAY_SECONDS = (6.0, 20.0)
"""A card's shortest and longest time on screen, before the player's "cards stay"."""
PER_CHARACTER = 0.04
"""Seconds of reading time per character, between those."""


def lifetime(card: Card, stay: float = 1.0) -> float:
    """How long ``card`` stays, in seconds: longer cards stay longer."""
    shortest, longest = STAY_SECONDS
    return min(longest, shortest + card.size * PER_CHARACTER) * stay


@dataclass(frozen=True, slots=True)
class Shown:
    card: Card
    until: float | None
    """When it goes, on the board's clock; None while pinned."""
    strip: bool = False
    """Shrunk to a strip with its title: a pinned card that gave way to a newer one."""

    @property
    def pinned(self) -> bool:
        return self.until is None


class Board:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        at_once: int = 1,
        stay: float = 1.0,
        quiet: bool = False,
        hidden: Collection[Kind] = (),
    ) -> None:
        self._clock = clock
        self.at_once = at_once
        self.stay = stay
        self.quiet = quiet
        self.hidden = frozenset(hidden)
        self._shown: list[Shown] = []
        """Newest first."""

    def allows(self, kind: Kind) -> bool:
        if kind in ALWAYS:
            return True
        if self.quiet:
            return kind in QUIET
        return kind not in self.hidden

    def show(self, card: Card, *, pinned: bool = False, seconds: float | None = None) -> bool:
        """Put ``card`` up. False if the player hid its kind: say it, don't show it."""
        if not self.allows(card.kind):
            return False
        if card.kind == "long":
            pinned = True
        if seconds is None and card.kind == "confirm":
            seconds = card.seconds
        self._make_room()
        until = None if pinned else self._clock() + (seconds or lifetime(card, self.stay))
        self._shown.insert(0, Shown(card, until))
        return True

    def _make_room(self) -> None:
        self._expire()
        if self.at_once <= 1:
            kept: list[Shown] = []
            for shown in self._shown:
                if shown.pinned and not shown.strip:
                    kept.append(replace(shown, strip=True))  # an older strip goes
            self._shown = kept[:1]
        else:
            self._shown = [s for s in self._shown if not s.strip][: self.at_once - 1]

    def pin(self) -> bool:
        """ "Keep that up": the newest card stays until cleared. False if there's none."""
        self._expire()
        for i, shown in enumerate(self._shown):
            if not shown.strip:
                self._shown[i] = replace(shown, until=None)
                return True
        return False

    def again(self) -> bool:
        """ "Show that again": a strip comes back as a full card. False if there's none."""
        self._expire()
        strips = [s for s in self._shown if s.strip]
        if not strips:
            return False
        self._shown.remove(strips[0])
        self.show(strips[0].card, pinned=True)
        return True

    def clear(self) -> None:
        self._shown.clear()

    def drop(self, kind: Kind) -> None:
        """Take down every card of ``kind``: a confirm card once its question is answered."""
        self._shown = [s for s in self._shown if s.card.kind != kind]

    def view(self) -> tuple[Shown, ...]:
        """What's on the Tile now, newest first. Empty means the Tile is invisible."""
        self._expire()
        return tuple(self._shown)

    def _expire(self) -> None:
        now = self._clock()
        self._shown = [s for s in self._shown if s.until is None or s.until > now]
