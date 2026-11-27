"""A pack's ``[route]`` table: plotting a route on the game's map by voice (#588, ADR 3's
amendments for it). Out of ``pack.py`` (ADR 19's line limit).

The table holds what the flow in ``runtime/route.py`` needs from the game: what the
player says to ask (``say``), where place names come from (``places``, a built-in part),
the keybind actions it presses, the map's words that text recognition looks for, where
the search panel's close button is, the kinds of log line it reads, and every line Tycho
says. Only a pack built into Tycho may have one: it clicks and types into the
game, so it's reviewed like code (ADR 3).
"""

import re
import string
from collections.abc import Collection
from dataclasses import MISSING, dataclass, fields, replace
from typing import Protocol, cast

from tycho.text import normalize

LINE_LIMIT = 100
"""The longest line Tycho may say about a route: one sentence or two, heard once."""
_ACTION = re.compile(r"[a-z][a-z0-9_]{0,63}")


class PlaceError(Exception):
    """The list of places couldn't be had: the message says why, for the log."""


class Places(Protocol):
    """Where the player may plot to: places' names as the game's map knows them."""

    async def find(self, said: str, system: str | None = None) -> str | None:
        """The place ``said`` most likely means, by its name on the map; None if none
        fits. Of places with the same name, the one in ``system`` (the player's, when
        the log shows it), as the map only searches there (#682). Raises ``PlaceError``
        when the list can't be had."""
        ...

    def guessed(self, said: str, name: str) -> bool:
        """Whether ``name`` is only a guess at what was ``said`` (misheard or misspelt),
        so Tycho asks before plotting (#650)."""
        ...

    def name_of(self, route_id: str) -> str | None:
        """The name of the place a log's route id is to, if the list knows it."""
        ...

    async def close(self) -> None:
        """Let go of any connection it holds, when the session closes (#644)."""
        ...


@dataclass(frozen=True, slots=True)
class RouteLines:
    """What Tycho says at each end, in the pack's words. ``{name}`` is the place's."""

    done: str
    replacing: str
    """Said before the new route's line when one was already set ({name} is its place)."""
    replacing_unnamed: str
    """The same, when the old route's place has no name Tycho knows."""
    on_foot: str
    no_map: str
    no_results: str
    not_listed: str
    stuck: str
    no_route: str
    other_place: str
    bad_name: str
    no_key: str = "A key the map needs isn't bound, so I didn't open it."
    unknown_place: str = "I don't know a place called {name}."
    no_places: str = "I can't get the list of places right now, so I didn't plot a route."
    trying: str = "I only plot routes in the game, not while you try things here."
    elsewhere: str = "The map can't plot a route to {name} from where you are."
    """Said for a place the player put somewhere the map doesn't reach (``Route.away``)."""
    not_typed: str = "The map's search box didn't take what I typed, so I left the map open."
    mistyped: str = "The search box shows {name} wrong. Clear it with its x, then ask again."
    """Said when the box shows the name wrong after typing ("TERMINNUS", 27 Nov 2026)."""
    box_full: str = (
        "The map's search box still has text in it. Clear it with its x, then ask again."
    )
    """Said when the box holds an earlier name, which typing would add to (27 Nov 2026)."""
    not_cleared: str = "I couldn't clear the map's search box, so I left the map open."
    """Said when a click on the box's own x didn't empty it (review of #734)."""
    risky_first: str = "I can't type {name}: its first letter is your {action} key."
    """Said when the name's first letter, typed before Tycho can see the box took the
    keys, would press a confirm-grade action (#682)."""
    other_system: str = "The map picked a {name} in another system, so I left it open for you."
    confirm: str = "Plot a route to {name}"
    """Asked before plotting a place that was only a guess (#650); "? Say confirm." follows."""


@dataclass(frozen=True, slots=True)
class RouteFlow:
    """A pack's route flow (ADR 3: only a built-in pack may declare one)."""

    nav: str
    open_map: str
    enter: str
    clear: str
    plot: str
    close_map: str
    search_box: str
    """The search box's words while it's empty ("SEARCH LOCATIONS...")."""
    results: str
    """A word over the search results once something is typed ("RESULTS (1)")."""
    no_results: str
    close_offset: tuple[float, float]
    """Where the panel's close button is from the middle of the search box's words, as
    fractions of the frame's height (the panel scales with it)."""
    close_marks: tuple[str, ...]
    """What text recognition may read the close button as ("x", "\u00d7")."""
    close_reach: float
    """How near, as a fraction of the frame's height, the read mark must be."""
    lines: RouteLines
    route_set: str
    """The fact for a route already set (the game state's)."""
    route_line: str
    """The kind of log line for a plotted route, whose ``name`` is the place's id."""
    seated: str
    """The kind of log line that only comes from a pilot's seat with the map open."""
    open_seconds: float = 3.0
    """How long the map may take to show its search box (ADR 3: at most 3 s)."""
    route_seconds: float = 4.0
    """How long the log may take to write the route line after the plot key."""
    beat_seconds: float = 0.3
    """After a key or click, for the game to act on it before the next one."""
    settle_seconds: float = 0.5
    """Once a frame shows the search box, before the next look: the map is still opening
    then, and a click lands on nothing (Ben's PC try, 25 Nov 2026). The box must be in
    the same place in two looks this far apart before it's clicked."""
    click_seconds: float = 0.5
    """After the click on the search box, before the first letter: the box takes keys a
    moment after it's clicked (#654, Ben's PC try)."""
    read_every: float = 0.25
    recent: str = ""
    """A word over the list the panel shows once the box has the keys, before anything is
    typed ("RECENT"): gone once a letter lands (Ben's PC try, 26 Nov 2026)."""
    calculating: str = ""
    """A word the map's panel shows while it works out the fuel to the place picked
    ("CALCULATING..."): R does nothing until it's gone (Ben's Hurston try, #682)."""
    other_system: str = ""
    """Words the place's panel shows when it's in another system ("TARGET IS IN ANOTHER
    SYSTEM"): R can't plot there (#682)."""
    calculating_seconds: float = 15.0
    """How long the map may take to work out the fuel before R."""
    read_width: float = 1.0
    """How much of the window, from its left edge, the map check reads: the search panel
    is on the left, and a narrower frame is read sooner, and at full size (#671)."""


@dataclass(frozen=True, slots=True)
class Route:
    """The ``[route]`` table: what asks for a route, where its places come from, and
    the flow."""

    say: tuple[str, ...]
    """What the player says first, then the place: "plot a route to"."""
    places: str
    """The built-in part that lists the places (``Registry.places``)."""
    flow: RouteFlow
    tool: str = ""
    """What the brain's ``plot_route`` tool says it's for, in the game's words (#647):
    the brain hands Tycho the place when the words missed ``say``. None: no tool."""
    here: frozenset[str] = frozenset()
    """Said after a place to put it where the player is ("in this system"): cut off, and
    the place plotted (#661). Normalised words."""
    away: frozenset[str] = frozenset()
    """Said after a place to put it where the map doesn't reach ("in the next system"):
    Tycho says ``lines.elsewhere`` and presses nothing (#661). Normalised words."""


_ACTIONS = ("nav", "open_map", "enter", "clear", "plot", "close_map")
_WORDS = ("search_box", "results", "no_results", "route_set", "route_line", "seated")
_KEYS = frozenset(
    {
        "say",
        "places",
        "close_offset",
        "close_marks",
        "close_reach",
        "lines",
        "tool",
        "here",
        "away",
        "recent",
        "calculating",
        "other_system",
        "read_width",
    }
)
_LINES = frozenset(f.name for f in fields(RouteLines))
_REQUIRED_LINES = frozenset(f.name for f in fields(RouteLines) if f.default is MISSING)


def read(
    value: object, where: str, built_in: bool, sees: bool, known: Collection[str]
) -> Route | None:
    """The ``[route]`` table checked, or None without one; ``ValueError`` says what's
    wrong with it. ``sees``: the pack has a window and a game-state part, which the flow
    reads. ``known``: the built-in places parts' ids."""
    if value is None:
        return None
    where = f"{where} [route]"
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be a table")
    if not built_in:
        raise ValueError(f"{where}: only a pack built into Tycho may plot routes")
    if not sees:
        raise ValueError(f"{where}: plotting a route needs a window and a game_state part")
    table = cast(dict[str, object], value)
    if unknown := sorted(set(table) - _KEYS - set(_ACTIONS) - set(_WORDS)):
        raise ValueError(f"{where}: unknown keys: {', '.join(unknown)}")
    say = _texts(table.get("say"), f"{where} say")
    places = _text(table.get("places"), f"{where} places")
    if places not in known:
        raise ValueError(f"{where}: Tycho has no built-in places part {places!r}")
    actions = {key: _text(table.get(key), f"{where} {key}") for key in _ACTIONS}
    if bad := [key for key, action in actions.items() if not _ACTION.fullmatch(action)]:
        raise ValueError(f"{where}: {', '.join(bad)} must name a keybind action")
    words = {key: _text(table.get(key), f"{where} {key}") for key in _WORDS}
    offset = table.get("close_offset")
    if not (
        isinstance(offset, list)
        and len(cast(list[object], offset)) == 2
        and all(_fraction(n) for n in cast(list[object], offset))
    ):
        raise ValueError(f"{where}: close_offset must be two fractions of the frame's height")
    across, down = cast(list[float], offset)
    reach = table.get("close_reach")
    if not (_fraction(reach) and cast(float, reach) > 0):
        raise ValueError(f"{where}: close_reach must be a fraction of the frame's height")
    flow = RouteFlow(
        nav=actions["nav"],
        open_map=actions["open_map"],
        enter=actions["enter"],
        clear=actions["clear"],
        plot=actions["plot"],
        close_map=actions["close_map"],
        search_box=words["search_box"],
        results=words["results"],
        no_results=words["no_results"],
        route_set=words["route_set"],
        route_line=words["route_line"],
        seated=words["seated"],
        close_offset=(float(across), float(down)),
        close_marks=_texts(table.get("close_marks"), f"{where} close_marks"),
        close_reach=float(cast(float, reach)),
        lines=_lines(table.get("lines"), f"{where} lines"),
    )
    recent = table.get("recent", "")
    if not isinstance(recent, str):
        raise ValueError(f"{where}: recent must be some text")
    calculating, other = table.get("calculating", ""), table.get("other_system", "")
    if not (isinstance(calculating, str) and isinstance(other, str)):
        raise ValueError(f"{where}: calculating and other_system must be some text")
    flow = replace(
        flow,
        recent=" ".join(recent.lower().split()),
        calculating=" ".join(calculating.lower().split()),
        other_system=" ".join(other.lower().split()),
    )
    width = table.get("read_width", 1.0)
    if not (_fraction(width) and 0 < cast(float, width) <= 1):
        raise ValueError(f"{where}: read_width must be a share of the window, above 0")
    flow = replace(flow, read_width=float(cast(float, width)))
    tool = table.get("tool", "")
    if not isinstance(tool, str):
        raise ValueError(f"{where}: tool must be some text")
    here, away = (_ends(table.get(key), f"{where} {key}") for key in ("here", "away"))
    return Route(say, places, flow, " ".join(tool.split()), here, away)


def _ends(value: object, where: str) -> frozenset[str]:
    """Words said after a place, normalised; none when the key is left out."""
    if value is None:
        return frozenset()
    return frozenset(" ".join(normalize(end)) for end in _texts(value, where))


def _lines(value: object, where: str) -> RouteLines:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be a table of lines")
    lines = cast(dict[str, object], value)
    if unknown := sorted(set(lines) - _LINES):
        raise ValueError(f"{where}: unknown lines: {', '.join(unknown)}")
    if missing := sorted(_REQUIRED_LINES - set(lines)):
        raise ValueError(f"{where}: missing lines: {', '.join(missing)}")
    for name, line in lines.items():
        if not (isinstance(line, str) and line.strip() and len(line) <= LINE_LIMIT):
            raise ValueError(f"{where}.{name}: needs a line of up to {LINE_LIMIT} characters")
        parsed = string.Formatter().parse(line)
        held = {"name", "action"} if name == "risky_first" else {"name"}
        if {field for _, field, _, _ in parsed if field is not None} - held:
            allowed = " and ".join(f"{{{field}}}" for field in sorted(held))
            raise ValueError(f"{where}.{name}: a line may hold only {allowed}")
    return RouteLines(**cast(dict[str, str], lines))


def _text(value: object, where: str) -> str:
    if not (isinstance(value, str) and value.strip()):
        raise ValueError(f"{where}: must be some text")
    return value.strip()


def _texts(value: object, where: str) -> tuple[str, ...]:
    items = cast(list[object], value) if isinstance(value, list) else []
    if not items or not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError(f"{where}: must be a list of text")
    return tuple(cast(str, item).strip() for item in items)


def _fraction(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and -1 <= value <= 1
