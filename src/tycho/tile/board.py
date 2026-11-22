"""What the Tile shows, and for how long (#2): the rules, with no window.

The drawing reads ``Board.view()``; everything that decides it lives here, so it is
tested without a screen:

- Cards fade by themselves, longer ones staying longer. A confirm card lasts as
  long as its question; a long answer, which the player asked to read, is pinned.
- One card at a time by default. A new card takes the place of the one showing, which
  shrinks to a strip with its title; the last two stay as strips for a while, a pinned
  one until cleared, and "show that again" brings the newest back (#491). A question
  or a notice that gives way just goes. With two at once, the oldest gives way and
  there are no strips.
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
STRIPS = 2
"""How many cards that gave way stay as strips, with one card at a time (#491)."""
STRIP_SECONDS = 60.0
"""How long a strip stays, before the player's "cards stay"; a pinned one stays."""
NO_STRIP: frozenset[Kind] = frozenset({"confirm", "notice"})
"""Kinds that just go when a new card comes: an answered question, a passing notice."""


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
    """Shrunk to a strip with its title: a card that gave way to a newer one."""
    since: float = 0.0
    """When it went up, on the board's clock: for the timer bar along its foot."""
    order: int = 0
    """When it was first shown, counted: going back and forward keep it (#491)."""

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
        self.hidden: frozenset[Kind] = frozenset(hidden)
        self._shown: list[Shown] = []
        """The full cards first, newest first, then the strips, newest first."""
        self._count = 0

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
        self._count += 1
        self._put(card, pinned=pinned, seconds=seconds, order=self._count)
        return True

    def _put(self, card: Card, *, pinned: bool, seconds: float | None, order: int) -> None:
        self._make_room()
        now = self._clock()
        until = None if pinned else now + (seconds or lifetime(card, self.stay))
        self._shown.insert(0, Shown(card, until, since=now, order=order))

    def _make_room(self) -> None:
        self._expire()
        if self.at_once <= 1:
            now = self._clock()
            strips: list[Shown] = []
            for shown in self._shown:
                if shown.strip:
                    strips.append(shown)
                elif shown.card.kind not in NO_STRIP:
                    until = None if shown.pinned else now + STRIP_SECONDS * self.stay
                    strips.append(replace(shown, strip=True, until=until, since=now))
            self._shown = sorted(strips, key=lambda s: s.order, reverse=True)[:STRIPS]
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
        """ "Show that again", "go back": the card before the one showing comes back in
        full and stays, and the one showing becomes a strip. With none showing, the
        newest strip comes back. False if there's nothing to go back to."""
        return self._step(back=True)

    def forward(self) -> bool:
        """ "Go forward": the card after the one showing, once the player went back."""
        return self._step(back=False)

    def _step(self, *, back: bool) -> bool:
        self._expire()
        full = [s.order for s in self._shown if not s.strip]
        here = full[0] if full else (None if back else 0)
        strips = [s for s in self._shown if s.strip]
        if back:
            found = [s for s in strips if here is None or s.order < here]
            target = max(found, key=lambda s: s.order, default=None)
        else:
            found = [s for s in strips if here is not None and s.order > here]
            target = min(found, key=lambda s: s.order, default=None)
        if target is None:
            return False
        self._shown.remove(target)
        self._put(target.card, pinned=True, seconds=None, order=target.order)
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
