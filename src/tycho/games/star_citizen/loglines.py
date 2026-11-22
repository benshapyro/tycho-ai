"""The Game.log lines Tycho understands, one pattern each (#30).

Each pattern is checked against a real line from the author's log in
``tests/test_gamelog.py``, because a patch can change the wording. A line no pattern
knows changes nothing: what Tycho doesn't recognise stays unknown, and commands that
need a fact refuse rather than guess.

Log text is untrusted data. It holds other players' names and chat-like notifications,
so whatever a pattern takes from a line is cleaned and cut here, names after "By" are
dropped, and anything that passes it on (the brain, the voice) must treat it as data,
never as instructions. Nothing else leaves a line: no handles, account or session ids,
server addresses or other players' names.

Surveyed on build 13220821 (22 Nov 2026: a 6.5-hour session and three earlier logs).
The log records: plotting a quantum route and arriving, letting go of a ship's
controls (there is still no line for taking them), the HUD's notifications (monitored
space, jurisdictions, armistice zones, private property), and joining a game server.
It has nothing about power, shields, fuel, gear, lights, the ship's modes, crimes, or
taking the controls; no mission lines appeared either (the session ran none). Those
need a look at the screen (#31) or a later survey.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

ROUTE = "route"
"""A quantum route is plotted (``now``)."""
SHIP = "ship"
"""The ship the player last had the controls of (``now``). Only the player's own
control-token lines name it: ships in ATC or navigation lines may be anyone's."""
ARRIVED = "arrived"
"""A quantum jump ended (an event)."""
LEFT_SEAT = "left_seat"
"""The player let go of a ship's controls (an event)."""
NOTIFICATION = "notification"
"""A line the HUD showed (an event). Can be about other players: untrusted text."""
MONITORED = "monitored"
"""In monitored space (``now``), from the HUD's "Entered/Exited Monitored Space"."""
JURISDICTION = "jurisdiction"
"""The last jurisdiction the HUD said the player entered (``now``)."""
JOINED = "joined"
"""Joined a game server (an event): a fresh start in the universe, so what was known
about the route and the surroundings is gone."""

LIMIT = 100
"""The most characters any text taken from the log may have."""


@dataclass(frozen=True, slots=True)
class Line:
    """What one line said, for ``GameLog`` to apply."""

    kind: str
    said: str = ""
    """In plain words, cleaned. Untrusted: from the log."""
    keep: bool = False
    """Also true now: kept under ``kind`` until something replaces or clears it."""
    clears: tuple[str, ...] = ()
    """What's no longer true now (kinds of kept lines)."""
    set_route: bool | None = None
    """True: a route is now plotted; False: it's gone; None: unchanged."""
    ship: str = ""
    """The player's own ship, from a control-token line."""


@dataclass(frozen=True, slots=True)
class Pattern:
    marker: bytes
    """Bytes every line of this kind holds: a cheap test before decoding it."""
    read: Callable[[str], Line | None]


_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u206f]")
_TIME = re.compile(r"^<(\d{4}-\d\d-\d\dT[\d:.]+Z)>")
_ROUTE_TO = re.compile(r"Successfully calculated route to (\S+)")
_RELEASED = re.compile(
    r"ClearDriver: Local client node \[\d+\] releasing control token for '([^']+)'"
)
_NOTIFICATION = re.compile(r'Added notification "(.*?)"\s*\[\d+\]')
_BY_SOMEONE = re.compile(r"\s+(?:by|from)\s+.*$", re.IGNORECASE)
_JURISDICTION = re.compile(r"^Entered (.+) Jurisdiction$")
_SHIP_NUMBER = re.compile(r"_\d+$")


def clean(text: str) -> str:
    """One line of plain text from the log, at most ``LIMIT`` characters."""
    line = " ".join(_CONTROL.sub(" ", text).replace("<", "").replace(">", "").split())
    return line if len(line) <= LIMIT else line[: LIMIT - 1].rstrip() + "…"


def place(name: str) -> str:
    """ "ObjectContainer_Lorville_City" is "Lorville City"."""
    for prefix in ("ObjectContainer_", "OC_"):
        name = name.removeprefix(prefix)
    return clean(name.replace("_", " "))


def ship(entity: str) -> str:
    """ "DRAK_Golem_909917463059" is "DRAK Golem": the ship's class, not this one's number."""
    return clean(_SHIP_NUMBER.sub("", entity).replace("_", " "))


def when(line: bytes | str) -> datetime | None:
    """The time the game logged ``line`` (UTC), if it starts with one."""
    text = line[:40].decode("ascii", "replace") if isinstance(line, bytes) else line[:40]
    if match := _TIME.match(text):
        try:
            return datetime.fromisoformat(match.group(1))
        except ValueError:
            return None
    return None


def _route(line: str) -> Line | None:
    if match := _ROUTE_TO.search(line):
        said = f"Quantum route plotted to {place(match.group(1))}"
        return Line(ROUTE, said, keep=True, set_route=True)
    return None


def _arrived(_line: str) -> Line | None:
    return Line(ARRIVED, "Quantum jump ended", clears=(ROUTE,), set_route=False)


def _left_seat(line: str) -> Line | None:
    if "<Vehicle Control Flow>" in line and (match := _RELEASED.search(line)):
        own = ship(match.group(1))
        said = f"Left the controls of the {own}"
        return Line(LEFT_SEAT, said, clears=(ROUTE,), set_route=False, ship=own)
    return None


def _notification(line: str) -> Line | None:
    if "<SHUDEvent_OnNotification>" not in line or not (match := _NOTIFICATION.search(line)):
        return None
    # "Quantum Travel Calibration Complete By <someone>": the name isn't Tycho's to repeat.
    said = clean(_BY_SOMEONE.sub("", match.group(1).rstrip(" :")))
    if not said:
        return None
    if said == "Entered Monitored Space":
        return Line(MONITORED, said, keep=True)
    if said == "Exited Monitored Space":
        return Line(NOTIFICATION, said, clears=(MONITORED,))
    if _JURISDICTION.match(said):
        return Line(JURISDICTION, said, keep=True)
    return Line(NOTIFICATION, said)


def _joined(line: str) -> Line | None:
    # The line names the server's address and shard: none of it is kept.
    if "<Join PU>" in line:
        said = "Joined a game server"
        return Line(JOINED, said, clears=(ROUTE, MONITORED, JURISDICTION), set_route=False)
    return None


PATTERNS = (
    Pattern(b"Successfully calculated route to", _route),
    Pattern(b"Arrived at Final Destination", _arrived),
    Pattern(b"ClearDriver", _left_seat),
    Pattern(b"Added notification", _notification),
    Pattern(b"<Join PU>", _joined),
)
"""Every line Tycho reads, in the order they're tried."""
MARKERS = tuple(pattern.marker for pattern in PATTERNS)


def read(line: bytes) -> Line | None:
    """What ``line`` says, if it's one Tycho understands."""
    for pattern in PATTERNS:
        if pattern.marker in line:
            return pattern.read(line.decode("utf-8", "replace"))
    return None
