"""The conversation: what was said lately, for follow-ups and "say that again" (#55).

Follow-ups now (ADR 10, ADR 14); memory later. It holds no threads and does no I/O.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from tycho.brain import Exchange
from tycho.runtime.messages import Result
from tycho.tile import Card

FOLLOW_UP_SECONDS = 30.0
"""Exchanges this recent go to the brain with the next request (ADR 10, ADR 14)."""
FOLLOW_UP_LIMIT = 4


@dataclass(frozen=True, slots=True)
class Line:
    """The last line for the player (#226): spoken, or shown in the console without a
    voice. Kept whether or not the voice managed to say it."""

    text: str = ""
    question: int | None = None
    """For the safety gate's confirm question: the request it asked about."""


def would_speak(result: Result) -> bool:
    """Whether this result has a line for the player, given a voice to say it."""
    if result.commands == ("say_again",):
        return result.done and bool(result.say)
    if not result.by_brain or not result.say.strip():
        return False
    return result.status == "replied" or (result.done and not result.announced)


class Conversation:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._recent: list[Exchange] = []
        self._recent_at = 0.0
        self.last_reply: tuple[str, Card | None] = ("", None)
        """The brain's last answer, for "show me that" (ADR 16)."""
        self.last_line = Line()
        """The last line for the player, for "say that again" (#222, #226)."""

    def follow_ups(self) -> tuple[Exchange, ...]:
        """The last few exchanges, if the latest was recent; otherwise a fresh start."""
        if self._clock() - self._recent_at > FOLLOW_UP_SECONDS:
            self._recent.clear()
        return tuple(self._recent)

    def heard(self, result: Result) -> None:
        """Take in how a request ended."""
        self._remember(result)
        if result.status == "replied":
            self.last_reply = (result.say, result.card)
        if result.commands != ("say_again",):  # a replay leaves the line it replayed
            if result.status == "asking" and result.question:
                self.last_line = Line(result.question, question=result.request)
            elif would_speak(result):
                self.last_line = Line(result.say)

    def _remember(self, result: Result) -> None:
        if not result.text or result.status in ("empty", "cancelled"):
            return
        if result.commands == ("say_again",):  # the answer it repeats is already there
            return
        if result.status == "replied":
            outcome = f"said: {result.say}"
        elif result.status == "asking":
            outcome = f"asked the player to confirm {', '.join(result.commands)}"
        elif result.done and result.pressed:
            outcome = f"did {', '.join(result.commands)} (pressed {', '.join(result.pressed)})"
        else:
            outcome = f"couldn't: {result.status}" + (f", {result.detail}" if result.detail else "")
        self._recent = [*self._recent, Exchange(result.text, outcome)][-FOLLOW_UP_LIMIT:]
        self._recent_at = self._clock()
