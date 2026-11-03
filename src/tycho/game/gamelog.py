"""What the game's own log says about the player's situation (architecture: log reader).

Star Citizen writes Game.log as it runs. Tycho only reads the file: no memory,
no hooks (ADR 3). The log records few things, so Tycho uses it for narrow
questions it can answer reliably, such as "has the pilot plotted a quantum route,
and are they still in the seat?". What the author's Game.log showed, 2026-10-31:
- "Successfully calculated route to ..." when the player plots a quantum route;
- "Arrived at Final Destination" when a jump ends;
- a line when the player leaves a pilot seat ("ClearDriver"), but none on entering;
- calibration notifications only for other players' ships, never the player's own;
- nothing about operator mode, NAV or SCM, or weapons.

Lines are matched by their wording, which a patch can change. Unrecognised lines
leave the state unknown, which makes commands that need it refuse.
"""

import threading
from pathlib import Path

QUANTUM_ROUTE = "quantum_route_set"
"""A quantum route is plotted and not yet flown, and the player hasn't left the pilot seat."""

_ROUTE_PLOTTED = b"Successfully calculated route to"
_ARRIVED = b"Arrived at Final Destination"
_LEFT_SEAT = (b"<Vehicle Control Flow>", b"ClearDriver")
_MARKERS = (_ROUTE_PLOTTED, _ARRIVED, _LEFT_SEAT[1])
"""A chunk with none of these needs no line-by-line look: most of the log is other news."""
_HEADER_BYTES = 256
"""The log's first lines name the session; a different start means the game restarted."""
MAX_BACKLOG_BYTES = 8 << 20
"""Read at most this much of what's new. A bigger backlog (hours of play since the last
command that needed the log) is skipped to its last part, forgetting what was known:
a fact from the skipped part might no longer hold, and not knowing only makes commands
that need it refuse. Bounded so a quantum command never waits seconds (audit, 2026-11-03)."""
_CHUNK = 1 << 20


class GameLog:
    """Satisfies ``runtime.ports.GameState``. Reads Game.log incrementally on each call."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._offset = 0
        self._header = b""
        self._partial = b""
        self._skipping = False
        self._facts: set[str] = set()
        self.readable = False
        """Whether the last read found the log."""

    def facts(self) -> frozenset[str]:
        """What's true now, as far as the log shows. Blocking: call it off the event loop."""
        with self._lock:
            self._catch_up()
            return frozenset(self._facts)

    def _catch_up(self) -> None:
        try:
            with self.path.open("rb") as file:
                header = file.read(_HEADER_BYTES)
                size = file.seek(0, 2)
                same = min(len(header), len(self._header))
                if not self._header or header[:same] != self._header[:same] or size < self._offset:
                    self._restart(header)  # a new game session, or the first read
                if size - self._offset > MAX_BACKLOG_BYTES:
                    self._restart(header)
                    self._offset = size - MAX_BACKLOG_BYTES
                    file.seek(self._offset - 1)
                    self._skipping = file.read(1) != b"\n"  # start at the next whole line
                file.seek(self._offset)
                while chunk := file.read(_CHUNK):
                    self._read(chunk)
        except OSError:
            self.readable = False
            self._restart(b"")
            return
        self.readable = True
        self._header = header

    def _read(self, chunk: bytes) -> None:
        self._offset += len(chunk)
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()  # the game may be mid-way through writing a line
        if self._skipping and lines:
            lines.pop(0)  # the end of a line whose start was skipped
            self._skipping = False
        if not any(marker in data for marker in _MARKERS):
            return
        for line in lines:
            self._apply(line)

    def _restart(self, header: bytes) -> None:
        self._header = header
        self._offset = 0
        self._partial = b""
        self._skipping = False
        self._facts.clear()

    def _apply(self, line: bytes) -> None:
        # Matched as bytes: decoding every line of a long session was most of the time.
        if _ROUTE_PLOTTED in line:
            self._facts.add(QUANTUM_ROUTE)
        elif _ARRIVED in line or all(part in line for part in _LEFT_SEAT):
            self._facts.discard(QUANTUM_ROUTE)
