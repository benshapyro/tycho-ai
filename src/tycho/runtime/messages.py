"""What passes between the stages of one request, and how long each stage took (ADR 11)."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

type Status = Literal[
    "done",
    "empty",  # too short, or nothing was said
    "not_understood",  # the fast path deferred; the brain arrives in 0.3
    "no_key",  # no key bound, or one Tycho can't press
    "refused",  # the safety gate said no
    "no_focus",  # Star Citizen wasn't in front, so nothing was pressed
    "cancelled",  # push-to-talk was pressed again, or Tycho is shutting down
    "failed",  # something broke; the loop carries on
]


class Trace:
    """Times each stage of one request, in milliseconds, from a monotonic clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = self._last = clock()
        self.stages: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        now = self._clock()
        self.stages[stage] = (now - self._last) * 1000
        self._last = now

    @property
    def total_ms(self) -> float:
        return (self._last - self._start) * 1000


@dataclass(frozen=True, slots=True)
class Result:
    """How one request ended. Logged, and turned into the done or not-done sound."""

    request: int
    status: Status
    heard: str = ""
    """What the transcriber heard, before the lexicon. Only logged if the player allows it."""
    text: str = ""
    """What was matched, after the lexicon."""
    pressed: tuple[str, ...] = ()
    """Game actions actually pressed, in order. Partial if a chain stopped part way."""
    detail: str = ""
    timings: dict[str, float] = field(default_factory=dict[str, float])
    total_ms: float = 0.0

    @property
    def done(self) -> bool:
        return self.status == "done"
