"""What the player said for the request being decided, for a look-up that must check
the player asked for it (the memory tool, ADR 8: a note is saved only after "remember
that..." or a yes to Tycho's offer). Set by ``rounds.traced`` around each request, so
every adapter and the eval set it the same way; look-ups started inside see it."""

import contextlib
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass

from tycho.brain.decisions import BrainRequest


@dataclass(frozen=True, slots=True)
class Heard:
    said: str
    """The player's words for this request."""
    before: str = ""
    """How the latest earlier exchange in the follow-up window ended ("said: Want me to
    remember that?"), or empty when this request starts fresh."""

    @classmethod
    def of(cls, request: BrainRequest) -> Heard:
        return cls(request.text, request.recent[-1].outcome if request.recent else "")


_HEARD: ContextVar[Heard | None] = ContextVar("heard", default=None)


def heard() -> Heard | None:
    """The request being decided, or None outside one."""
    return _HEARD.get()


@contextlib.contextmanager
def hearing(now: Heard | None) -> Generator[None]:
    token = _HEARD.set(now)
    try:
        yield
    finally:
        _HEARD.reset(token)
