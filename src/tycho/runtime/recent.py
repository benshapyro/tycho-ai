"""The last exchanges, for Home in Tycho's window (ADR 17, amended 15 Nov 2026).

Kept in memory only: never written to a file because of this, handed to nobody but the
window, and gone when Tycho quits. Keeping what was said on disk is still only the
``transcripts`` setting.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from tycho.runtime.messages import Result

SIZE = 20
"""How many exchanges are kept."""

_WORDS = {
    "not_understood": "Didn't understand that.",
    "no_key": "No key is bound for that.",
    "refused": "Didn't do it: not safe right now.",
    "no_focus": "The game wasn't in front, so nothing was pressed.",
    "failed": "Something went wrong.",
}
"""What Tycho did, when the result carries no words of its own."""


@dataclass(frozen=True, slots=True)
class Exchange:
    at: float
    """When it ended, in seconds since 1970 (``time.time``)."""
    said: str
    """What the player said, as Tycho understood it."""
    did: str
    """What Tycho did or said back, in a line."""
    ok: bool
    """Tycho did it, answered, or asked to confirm."""


def exchange(result: Result, at: float) -> Exchange | None:
    """``result`` as Home shows it; None for one with nothing said (too short, cancelled)."""
    if not result.text.strip() or result.status in ("empty", "cancelled"):
        return None
    ok = result.status in ("done", "replied", "asking")
    if result.would_press:  # setup's Try it: nothing reached the game (ADR 17)
        did = f"Would press {_joined(result.would_press)}."
        if result.say:
            did = f"{result.say} ({did})"
    elif result.status == "asking":
        did = result.question or "Asked you to confirm."
    elif result.status in ("done", "replied"):
        did = result.say or ("Done." if result.status == "done" else "")
    elif result.status == "refused":
        did = result.detail or _WORDS["refused"]  # the safety gate's own words, for players
    else:
        # Other details are for the log: action ids and error text (review of #459).
        did = _WORDS.get(result.status, "")
    return Exchange(at, " ".join(result.text.split()), " ".join(did.split()), ok)


def _joined(names: tuple[str, ...]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


class Recent:
    """The last ``SIZE`` exchanges, newest first. ``add`` is the session's ``on_result``;
    the window reads ``exchanges`` from its own threads."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: deque[Exchange] = deque(maxlen=SIZE)

    def add(self, result: Result) -> None:
        shown = exchange(result, self._clock())
        if shown is not None:
            with self._lock:
                self._kept.appendleft(shown)

    def exchanges(self) -> list[Exchange]:
        with self._lock:
            return list(self._kept)
