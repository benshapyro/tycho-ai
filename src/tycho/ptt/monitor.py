"""Device-independent push-to-talk logic, polled from a single thread."""

import time
from collections.abc import Callable, Iterable, Sequence
from enum import Enum, auto
from typing import Protocol

from tycho.ptt.binding import Binding


class InputSource(Protocol):
    """A family of physical buttons, such as keyboard and mouse, or joysticks."""

    def pressed(self) -> frozenset[Binding]:
        """Every button from this source that is held right now."""
        ...

    def is_pressed(self, binding: Binding) -> bool:
        """Whether one button is held right now. Cheaper than ``pressed()``."""
        ...


class Edge(Enum):
    PRESSED = auto()
    RELEASED = auto()


class PushToTalk:
    """Turns polled button state into press and release edges for one binding."""

    def __init__(self, binding: Binding, sources: Sequence[InputSource]) -> None:
        self.binding = binding
        self._sources = tuple(sources)
        self._held = False

    def poll(self) -> Edge | None:
        held = any(source.is_pressed(self.binding) for source in self._sources)
        if held == self._held:
            return None
        self._held = held
        return Edge.PRESSED if held else Edge.RELEASED


def capture_binding(
    sources: Iterable[InputSource],
    *,
    ignore: frozenset[Binding] = frozenset(),
    poll_interval: float = 0.005,
    timeout: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Binding | None:
    """Wait for the user to press a button, and return it.

    Buttons already held when this starts are ignored until released, so the key
    that launched the prompt isn't captured. Returns None on timeout.
    """
    sources = tuple(sources)
    held_at_start = _all_pressed(sources)
    deadline = None if timeout is None else clock() + timeout
    while True:
        now = _all_pressed(sources)
        held_at_start &= now
        fresh = now - held_at_start - ignore
        if fresh:
            return min(fresh, key=str)
        if deadline is not None and clock() >= deadline:
            return None
        sleep(poll_interval)


def _all_pressed(sources: Iterable[InputSource]) -> frozenset[Binding]:
    return frozenset[Binding]().union(*(source.pressed() for source in sources))
