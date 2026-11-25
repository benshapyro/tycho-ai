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

Lines are matched by their wording, which a patch can change: the patterns are in
``loglines.py``, each tested against a real line. Unrecognised lines leave the state
unknown, which makes commands that need it refuse.

Besides the gate's facts, the reader keeps what's true now (the route, the player's
last ship) and the latest events (a jump ended, the HUD's notifications), each with
the time the game logged it: ``situation()`` (#30), for what Tycho tells the brain
and says on its own.
"""

import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from tycho.games.star_citizen import loglines
from tycho.situation import Logged, Situation

QUANTUM_ROUTE = "quantum_route_set"
"""A quantum route is plotted and not yet flown, and the player hasn't left the pilot seat."""

RECENT = 50
"""How many of the latest events ``situation()`` keeps."""
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

    def __init__(self, path: Path, names: Callable[[str], str] | None = None) -> None:
        """``names``: the game's text for a key such as "item_name_x" ("" if none), for
        blueprints the log names only by key (#598)."""
        self.path = path
        self._names = names
        self._lock = threading.Lock()
        self._offset = 0
        self._header = b""
        self._partial = b""
        self._skipping = False
        self._facts: set[str] = set()
        self._now: dict[str, Logged] = {}
        self._recent: deque[Logged] = deque(maxlen=RECENT)
        self._last: dict[str, Logged] = {}
        self._number = 0
        self.readable = False
        """Whether the last read found the log."""

    def facts(self) -> frozenset[str]:
        """What's true now, as far as the log shows. Blocking: call it off the event loop."""
        with self._lock:
            self._catch_up()
            return frozenset(self._facts)

    def situation(self) -> Situation:
        """The facts, what's true now and the latest events. Blocking, like ``facts``."""
        with self._lock:
            self._catch_up()
            return Situation(frozenset(self._facts), tuple(self._now.values()), tuple(self._recent))

    def last(self, kind: str) -> Logged | None:
        """The newest line of ``kind`` (a quiet one too), if one was read since the game
        started. Blocking, like ``facts``. The route flow compares its ``number`` with
        one it saw before acting, to tell a new line from an old one (#588)."""
        with self._lock:
            self._catch_up()
            return self._last.get(kind)

    def newest(self) -> int:
        """The number the newest line read so far has (0 for none). Blocking."""
        with self._lock:
            self._catch_up()
            return self._number

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
        # A chunk with no marker needs no line-by-line look: most of the log is other news.
        if not any(marker in data for marker in loglines.MARKERS):
            return
        for line in lines:
            self._apply(line)

    def _restart(self, header: bytes) -> None:
        self._header = header
        self._offset = 0
        self._partial = b""
        self._skipping = False
        self._facts.clear()
        self._now.clear()
        self._recent.clear()
        self._last.clear()

    def _apply(self, raw: bytes) -> None:
        # Matched as bytes first: decoding every line of a long session was most of the time.
        line = loglines.read(raw)
        if line is None:
            return
        if line.key and self._names is not None:
            line = loglines.named_blueprint(line, self._names(line.key))
        self._number += 1
        logged = Logged(line.kind, line.said, loglines.when(raw), self._number, line.name)
        self._last[line.kind] = logged
        if line.quiet:
            return
        if line.set_route is True:
            self._facts.add(QUANTUM_ROUTE)
        elif line.set_route is False:
            self._facts.discard(QUANTUM_ROUTE)
        for kind in line.clears:
            self._now.pop(kind, None)
        if line.keep:
            self._now.pop(line.kind, None)  # the newest last
            self._now[line.kind] = logged
        if line.ship:
            said = f"Last at the controls of the {line.ship}"
            self._now.pop(loglines.SHIP, None)
            self._now[loglines.SHIP] = Logged(loglines.SHIP, said, logged.at, self._number)
        if line.system:  # where the brain's lookups look (#656)
            said, system = f"In the {line.system} system", loglines.SYSTEM
            self._now.pop(system, None)
            self._now[system] = Logged(system, said, logged.at, self._number, line.system)
        self._recent.append(logged)
