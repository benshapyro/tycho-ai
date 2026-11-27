"""What the route flow needs from the PC: the words on the game's frames, a click, and
the game's log (``route``, #588). Interfaces only."""

from typing import Protocol, runtime_checkable

from tycho.eyes.words import Reading
from tycho.situation import Logged


class ScreenReader(Protocol):
    """The words on one fresh frame of the game window, read on the PC (``eyes.words``).
    Blocking. Raises ``CaptureError`` with why, if there's no frame."""

    def read(self) -> Reading: ...


@runtime_checkable
class Warm(Protocol):
    """A reader that can get ready before the map opens, and be put away after: one
    route's looks then share one reading process (``eyes.loop``, #661). Blocking."""

    def warm(self) -> None: ...

    def finish(self) -> None: ...


class Clicker(Protocol):
    """One left click at a spot in a frame of ``window`` (``frame`` is its size), only if
    that window is in front and the frame's size. Blocking. "" if it clicked, else why not."""

    def click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str: ...


@runtime_checkable
class RouteLog(Protocol):
    """What the route flow reads from the game's log (``GameLog``). Blocking."""

    def facts(self) -> frozenset[str]: ...

    def last(self, kind: str) -> Logged | None: ...

    def newest(self) -> int: ...
