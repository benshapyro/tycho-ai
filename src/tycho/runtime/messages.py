"""What passes between the stages of one request, and how long each stage took (ADR 11)."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from tycho.tile import Card

type Status = Literal[
    "done",
    "empty",  # too short, or nothing was said
    "not_understood",  # neither the fast path nor the brain could act on it
    "replied",  # the brain answered or asked something; nothing was pressed
    "no_key",  # no key bound, or one Tycho can't press
    "refused",  # the safety gate said no
    "asking",  # confirm-grade: waiting for the player to say "confirm" (ADR 13)
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

    def elapsed_ms(self) -> float:
        """Time since the request started, now. Safe to call from another thread."""
        return (self._clock() - self._start) * 1000


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
    commands: tuple[str, ...] = ()
    """The commands matched or picked, one per part of the request."""
    by_brain: bool = False
    """The brain chose the commands, or wrote the reply."""
    say: str = ""
    """What Tycho says about it: the brain's reply, or its line about what it pressed."""
    announced: bool = False
    """The game itself announces everything that was done, so Tycho needn't."""
    question: str = ""
    """For "asking": what Tycho asks, from the gate's own words ("Eject? Say confirm.")."""
    card: Card | None = None
    """The details of a reply, for the Tile (#2)."""
    confirmed: bool = False
    """The player said "confirm" to a question Tycho asked about this request."""
    toggled: bool = False
    """Toggles were pressed for a direction, and ``say`` says Tycho can't see which way
    they went (#29): said even for the fast path, unless the game announces it."""
    would_press: tuple[str, ...] = ()
    """Setup's Try it (ADR 17): each key Tycho would have pressed, as the player knows
    it ("Landing System (N)"). Nothing was sent: ``pressed`` stays empty."""
    timings: dict[str, float] = field(default_factory=dict[str, float])
    total_ms: float = 0.0
    keydown_ms: float | None = None
    """From releasing push-to-talk to the first key the game acts on going down: what
    the 0.3 s target is about (#58). None if nothing was pressed."""

    @property
    def done(self) -> bool:
        return self.status == "done"


@dataclass(slots=True)
class Progress:
    """How far one request got, so even a cancelled one is logged with what it pressed."""

    commands: tuple[str, ...] = ()
    pressed: list[str] = field(default_factory=list[str])
    confirmed: bool = False
    by_brain: bool = False
    say: str = ""
    announced: bool = False
    question: str = ""
    keydown_ms: float | None = None
    card: Card | None = None
    would_press: list[str] = field(default_factory=list[str])
    toggled: bool = False

    def result(
        self,
        request: int,
        status: Status,
        trace: Trace,
        heard: str = "",
        text: str = "",
        detail: str = "",
    ) -> Result:
        """How the request ended, with everything it got to."""
        return Result(
            request=request,
            status=status,
            heard=heard,
            text=text,
            pressed=tuple(self.pressed),
            detail=detail,
            commands=self.commands,
            confirmed=self.confirmed,
            by_brain=self.by_brain,
            say=self.say,
            announced=self.announced,
            question=self.question,
            card=self.card,
            timings=dict(trace.stages),
            total_ms=trace.total_ms,
            keydown_ms=self.keydown_ms,
            would_press=tuple(self.would_press),
            toggled=self.toggled,
        )
