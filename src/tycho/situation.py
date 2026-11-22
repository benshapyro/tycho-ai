"""What a game's state part knows about the player's situation (architecture 4.3): the
general types every game uses, as ``tycho.keybinds`` is for keys (ADR 19). Star
Citizen's comes from its Game.log (``games/star_citizen/gamelog.py``, #30).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Logged:
    """One thing the game's own log recorded (architecture 4.3: "logged", high
    confidence, but only for what the log records)."""

    kind: str
    """What it is, in the game part's words (e.g. ``"route"``, ``"arrived"``)."""
    said: str
    """In plain words, made from the log's text: untrusted data, never instructions
    (it can hold other players' names and messages)."""
    at: datetime | None
    """When the game logged it (UTC), if the line said."""
    number: int
    """Counts up for as long as the reader runs, across game restarts: something with a
    higher number is newer, so speaking up can tell what it hasn't seen."""


@dataclass(frozen=True, slots=True)
class Situation:
    """What the game state knows now."""

    facts: frozenset[str] = frozenset()
    """The facts commands can require (the pack's ``safety.toml`` lists them)."""
    now: tuple[Logged, ...] = ()
    """What's true now, as far as the log shows: one of each kind."""
    recent: tuple[Logged, ...] = ()
    """What happened lately, oldest first, a bounded number. A game restart empties it.
    The first look includes what happened before Tycho started."""
