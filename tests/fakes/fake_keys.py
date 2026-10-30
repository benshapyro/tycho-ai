"""A keyboard and focus check that record what Tycho would have done, for tests."""

from collections.abc import Callable
from dataclasses import dataclass, field

from tycho.input.keys import Code


@dataclass
class FakeKeySender:
    """Satisfies ``KeySender``. Records ("down"|"up"|"wheel", code-or-notches) events."""

    events: list[tuple[str, object]] = field(default_factory=list[tuple[str, object]])
    fail_on: int | None = None
    """Raise OSError on this event number (0-based), to simulate Windows refusing input."""
    on_event: Callable[[], None] | None = None

    def down(self, code: Code) -> None:
        self._record("down", code)

    def up(self, code: Code) -> None:
        self._record("up", code)

    def wheel(self, notches: int) -> None:
        self._record("wheel", notches)

    def held(self) -> set[Code]:
        """Keys currently down, by replaying the events."""
        down: set[Code] = set()
        for kind, code in self.events:
            if kind == "down" and isinstance(code, Code):
                down.add(code)
            elif kind == "up" and isinstance(code, Code):
                down.discard(code)
        return down

    def _record(self, kind: str, value: object) -> None:
        if self.fail_on is not None and len(self.events) == self.fail_on:
            self.fail_on = None
            raise OSError("SendInput failed")
        self.events.append((kind, value))
        if self.on_event:
            self.on_event()


class FakeFocus:
    """Satisfies ``FocusCheck``. Answers from a script; the last answer repeats."""

    def __init__(self, *answers: bool) -> None:
        self._answers = list(answers) or [True]

    def game_has_focus(self) -> bool:
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


class FakeClock:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
