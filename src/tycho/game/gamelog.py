"""What the game's own log says about the player's situation (architecture: log reader).

Star Citizen writes Game.log as it runs. Tycho only reads the file: no memory,
no hooks (ADR 3). The log records few things, so Tycho uses it for narrow
questions it can answer reliably, such as "is the quantum drive calibrated, with
the player still in the pilot seat?". What it saw in Game.log, 2026-10-31:
- a HUD notification when quantum calibration starts and when it completes;
- a line when the player leaves a pilot seat ("ClearDriver"), but none on entering;
- nothing about operator mode, NAV or SCM, or weapons.

A cancelled calibration isn't logged, so "calibrated" can be stale. Commands that
rely on it must be harmless when it is.

Lines are matched by their wording, which a patch can change. Unrecognised lines
leave the state unknown, which makes commands that need it refuse.
"""

import threading
from pathlib import Path

QUANTUM_CALIBRATED = "quantum_calibrated"
"""Quantum calibration completed, and the player hasn't left the pilot seat since."""

_CALIBRATION_STARTED = 'Added notification "Quantum Travel Calibration Started'
_CALIBRATION_COMPLETE = 'Added notification "Quantum Travel Calibration Complete'
_LEFT_SEAT = ("<Vehicle Control Flow>", "ClearDriver")
_HEADER_BYTES = 256
"""The log's first lines name the session; a different start means the game restarted."""


class GameLog:
    """Satisfies ``runtime.ports.GameState``. Reads Game.log incrementally on each call."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._offset = 0
        self._header = b""
        self._partial = b""
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
                file.seek(self._offset)
                data = file.read()
        except OSError:
            self.readable = False
            self._restart(b"")
            return
        self.readable = True
        self._header = header
        self._offset += len(data)
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()  # the game may be mid-way through writing a line
        for line in lines:
            self._apply(line.decode("utf-8", "replace"))

    def _restart(self, header: bytes) -> None:
        self._header = header
        self._offset = 0
        self._partial = b""
        self._facts.clear()

    def _apply(self, line: str) -> None:
        if _CALIBRATION_COMPLETE in line:
            self._facts.add(QUANTUM_CALIBRATED)
        elif _CALIBRATION_STARTED in line or all(part in line for part in _LEFT_SEAT):
            self._facts.discard(QUANTUM_CALIBRATED)
