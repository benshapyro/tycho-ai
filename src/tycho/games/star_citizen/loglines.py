"""The Game.log lines Tycho understands, one pattern each (#30).

Each pattern is checked against a real line from the author's log in
``tests/test_gamelog.py``, because a patch can change the wording. A line no pattern
knows changes nothing: what Tycho doesn't recognise stays unknown, and commands that
need a fact refuse rather than guess.

Log text is untrusted data. It holds other players' names and chat-like notifications,
so only notifications of a known shape are kept (``KNOWN_NOTIFICATIONS``: party invites,
friend requests and other players' calibrations start or end with a name and are
dropped, #531), whatever a pattern takes from a line is cleaned and cut here, and
anything that passes it on (the brain, the voice) must treat it as data, never as
instructions. Nothing else leaves a line: no handles, account or session ids, server
addresses or other players' names.

Surveyed on build 13220821 (22 Nov 2026: a 6.5-hour session and three earlier logs).
The log records: plotting a quantum route and arriving, letting go of a ship's
controls (there is still no line for taking them), the HUD's notifications (monitored
space, jurisdictions, armistice zones, private property), and joining a game server.
It has nothing about power, shields, fuel, gear, lights, the ship's modes, crimes, or
taking the controls; no mission lines appeared either (the session ran none). Those
need a look at the screen (#31) or a later survey.

A later look (23 Nov 2026, a night of missions on the same build) found the HUD's
"Received Blueprint: <item>" notification, logged about a second after the contract's
"Contract Complete" one (#598). It doesn't name the mission, and some items come as the
game's text key ("@item_Name_..."), which the reader turns into words if it can.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

ROUTE = "route"
"""A quantum route is plotted (``now``); ``name`` is the game's id for the place
("levski_all-001"), which the route flow checks (#588)."""
NO_ROUTE_ON_MAP = "no_route_on_map"
"""A ship's star map was opened with no route set (quiet): "No Route loaded!". The only
line that shows the player is in a ship's seat with the map open, so the route flow
reads it (#588). It repeats while the map stays open."""
SHIP = "ship"
"""The ship the player last had the controls of (``now``). Only the player's own
control-token lines name it: ships in ATC or navigation lines may be anyone's."""
ARRIVED = "arrived"
"""A quantum jump ended (an event)."""
LEFT_SEAT = "left_seat"
"""The player let go of a ship's controls (an event)."""
MONITORED = "monitored"
"""In monitored space (``now``), from the HUD's "Entered Monitored Space"."""
LEFT_MONITORED = "left_monitored"
"""The HUD said "Exited Monitored Space" (an event)."""
JURISDICTION = "jurisdiction"
"""The last jurisdiction the HUD said the player entered (``now``). The survey found no
line for leaving one, so it lasts until the next one or a server join (#531)."""
ARMISTICE = "armistice"
"""The HUD said "Entering Armistice Zone - Combat Prohibited" (an event: there's no line
for leaving one, so it can't be kept as true)."""
PRIVATE_PROPERTY = "private_property"
"""The HUD said "Entering Private Property" (an event)."""
LEFT_PRIVATE_PROPERTY = "left_private_property"
"""The HUD said "Leaving Private Property" (an event)."""
BLUEPRINT = "blueprint"
"""The HUD said a blueprint was received (an event); ``name`` is the item's."""
BLUEPRINT_UNNAMED = "blueprint_unnamed"
"""A blueprint was received, but the log gave only the item's text key (``key``), and
it couldn't be turned into words (an event)."""
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
    name: str = ""
    """One short name the line carries (a jurisdiction's, a blueprint's item), for a
    callout's line (#4)."""
    key: str = ""
    """The game's text key for ``name``, when the log gave a key instead of words
    (``BLUEPRINT_UNNAMED``): the reader looks it up in the game's own text."""
    quiet: bool = False
    """Not news: kept only as the newest of its kind (``GameLog.last``), never in what
    the brain or a callout hears."""


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
_JURISDICTION = re.compile(r"^Entered ([A-Za-z][A-Za-z&' ]{0,30}) Jurisdiction$")
"""A jurisdiction's name is a place's or a faction's ("UEE", "Rough & Ready", "People's
Alliance"): letters, "&" and apostrophes, at most four words. A callout says it (#4)."""
_SHIP_NUMBER = re.compile(r"_\d+$")
_BLUEPRINT = re.compile(r"^Received Blueprint: (.+)$")
ITEM_NAME = re.compile(r"""[A-Za-z0-9][A-Za-z0-9 "'.&()/+-]{0,39}""")
"""An item's name as a callout may say it ("Killshot "Dominion Camo" Rifle", "Monde
Helmet Hiro"): letters, digits and a few marks, at most 40, the longest a callout says."""
_ITEM_KEY = re.compile(r"@(item_name_?[A-Za-z0-9_]{1,80})", re.IGNORECASE)
_LONGEST_BLUEPRINT = len("Received Blueprint: @item_name_") + 80


def clean(text: str, limit: int = LIMIT) -> str:
    """One line of plain text from the log, at most ``limit`` characters."""
    line = " ".join(_CONTROL.sub(" ", text).replace("<", "").replace(">", "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


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
        return Line(ROUTE, said, keep=True, set_route=True, name=clean(match.group(1)))
    return None


def _no_route_on_map(line: str) -> Line | None:
    # Names the ship's entity and the game's internal ids: none of it is kept.
    if "GetStarmapRouteSegmentData" in line and "No Route loaded!" in line:
        return Line(NO_ROUTE_ON_MAP, "The star map has no route", quiet=True)
    return None


def _arrived(_line: str) -> Line | None:
    return Line(ARRIVED, "Quantum jump ended", clears=(ROUTE,), set_route=False)


def _left_seat(line: str) -> Line | None:
    # Any such line clears the route, whatever its exact shape: a route kept while the
    # player is on foot would let "engage quantum" click the handgun's trigger (ADR 12).
    # The full shape is only needed for the ship's name.
    if "<Vehicle Control Flow>" not in line:
        return None
    match = _RELEASED.search(line)
    own = ship(match.group(1)) if match else ""
    said = f"Left the controls of the {own}" if own else "Left a ship's controls"
    return Line(LEFT_SEAT, said, clears=(ROUTE,), set_route=False, ship=own)


KNOWN_NOTIFICATIONS: dict[str, str] = {
    "Entered Monitored Space": MONITORED,
    "Exited Monitored Space": LEFT_MONITORED,
    "Entering Armistice Zone - Combat Prohibited": ARMISTICE,
    "Entering Private Property": PRIVATE_PROPERTY,
    "Leaving Private Property": LEFT_PRIVATE_PROPERTY,
}
"""The HUD's notifications Tycho keeps, by their exact wording (with "Entered ...
Jurisdiction", below), each seen in the survey. Every other notification is dropped:
some start or end with another player's name, and none of those may reach the brain or
the voice (#531). A new kind joins here with a real line in ``tests/test_gamelog.py``."""


def _notification(line: str) -> Line | None:
    if "<SHUDEvent_OnNotification>" not in line or not (match := _NOTIFICATION.search(line)):
        return None
    text = match.group(1).rstrip(" :")
    said = clean(text)
    if kind := KNOWN_NOTIFICATIONS.get(said):
        if kind == MONITORED:
            return Line(MONITORED, said, keep=True)
        if kind == LEFT_MONITORED:
            return Line(LEFT_MONITORED, said, clears=(MONITORED,))
        return Line(kind, said)
    # Matched before cutting: a long item key cut to "…" fits neither pattern (#605).
    if match := _BLUEPRINT.match(clean(text, limit=_LONGEST_BLUEPRINT)):
        return _blueprint(match.group(1).strip())
    if (match := _JURISDICTION.match(said)) and len(match.group(1).split()) <= 4:
        return Line(JURISDICTION, said, keep=True, name=match.group(1))
    return None


def _blueprint(item: str) -> Line | None:
    if key := _ITEM_KEY.fullmatch(item):
        return Line(BLUEPRINT_UNNAMED, "Received a blueprint", key=key.group(1).lower())
    if ITEM_NAME.fullmatch(item):
        return Line(BLUEPRINT, f"Received the {item} blueprint", name=item)
    return None  # not an item's name: never said


def named_blueprint(line: Line, name: str) -> Line:
    """``line`` (``BLUEPRINT_UNNAMED``) with its item's ``name`` from the game's text,
    or unchanged if the name isn't one a callout may say."""
    name = clean(name)
    if not ITEM_NAME.fullmatch(name):
        return line
    return Line(BLUEPRINT, f"Received the {name} blueprint", name=name)


def _joined(line: str) -> Line | None:
    # The line names the server's address and shard: none of it is kept.
    if "<Join PU>" in line:
        said = "Joined a game server"
        return Line(JOINED, said, clears=(ROUTE, MONITORED, JURISDICTION), set_route=False)
    return None


PATTERNS = (
    Pattern(b"Successfully calculated route to", _route),
    Pattern(b"Arrived at Final Destination", _arrived),
    Pattern(b"<Failed to get starmap route data!>", _no_route_on_map),
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
