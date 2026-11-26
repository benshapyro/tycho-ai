"""Device-independent push-to-talk logic, polled from a single thread."""

import threading
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
    """Turns polled button state into press and release edges for one or more bindings:
    holding any of them is holding push-to-talk (#621), so moving from the mouse button
    to a stick button mid-sentence is one long press."""

    def __init__(
        self, bindings: Binding | Sequence[Binding], sources: Sequence[InputSource]
    ) -> None:
        self.bindings = (bindings,) if isinstance(bindings, Binding) else tuple(bindings)
        if not self.bindings:
            raise ValueError("push-to-talk needs at least one button")
        self._sources = tuple(sources)
        self._held = False
        self._next: tuple[tuple[Binding, ...], tuple[InputSource, ...]] | None = None
        """Buttons chosen in the app while Tycho runs (#646), taken up by the next poll."""
        self._lock = threading.Lock()

    @property
    def binding(self) -> Binding:
        """The first button."""
        return self.bindings[0]

    def rebind(self, bindings: Sequence[Binding], sources: Sequence[InputSource]) -> None:
        """Listen to these buttons from the next poll on (#646): push-to-talk chosen again
        in the app applies at once. Safe from any thread; the old sources are closed on
        the polling thread, since SDL must be let go where it was set up. Held across the
        change, push-to-talk is released unless a new button is held too."""
        if not bindings:
            raise ValueError("push-to-talk needs at least one button")
        with self._lock:
            self._next = (tuple(bindings), tuple(sources))

    def poll(self) -> Edge | None:
        with self._lock:
            chosen, self._next = self._next, None
        if chosen is not None:
            old, (self.bindings, self._sources) = self._sources, chosen
            for source in old:
                close = getattr(source, "close", None)
                if close is not None:
                    close()
        held = any(
            source.is_pressed(binding) for binding in self.bindings for source in self._sources
        )
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
    cancelled: Callable[[], bool] = lambda: False,
) -> Binding | None:
    """Wait for the user to press a button, and return it.

    Buttons already held when this starts are ignored until released, so the key
    that launched the prompt isn't captured. Returns None on timeout, or once
    ``cancelled`` says so (the player left the app window's capture).
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
        if cancelled() or (deadline is not None and clock() >= deadline):
            return None
        sleep(poll_interval)


def _all_pressed(sources: Iterable[InputSource]) -> frozenset[Binding]:
    return frozenset[Binding]().union(*(source.pressed() for source in sources))
