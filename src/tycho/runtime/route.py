"""Plotting a route on the game's map by voice (#588, ADR 3's three amendments for it,
ADR 14's "a local check before a press").

The flow, as the pack declares it (``RouteFlow``):

1. set the mode that takes the weapons offline (``nav``), so a click that misses the map
   can't fire;
2. open the map (``open_map``) and wait, at most ``open_seconds``, for a frame that shows
   its search box, and for the log to show the player is in a pilot's seat: a route
   already set (``route_set``), or the map's "no route" line (``seated``) since step 1;
3. click the search box, in that frame (the first click);
4. type the place's name, letter by letter, and press Enter (``enter``);
5. read a fresh frame: the typed name, the results, and the chosen place among them;
6. click the panel's close button (the second, last click), found from the search box
   by the pack's offset and only if text recognition agrees something is there;
7. clear the route (``clear``) if one is set, then plot (``plot``);
8. wait for the log's route line, and check its id names the chosen place;
9. close the map (``close_map``).

Every key goes through the safety gate (``plan_request``), so with key actions off, or a
key that clashes with something dangerous, nothing is pressed. Every press and click
checks the game window is in front. Any step that fails stops the flow there: nothing
more is pressed, and the map is left open for the player (except on foot, where it's
closed again, having shown nothing). One request types one name once, and never tries
again on its own.

Blocking: the session runs ``plot`` on the key thread. A cancel (push-to-talk pressed
again) stops it between steps.
"""

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from tycho.commands import Command
from tycho.eyes import CaptureError
from tycho.eyes.words import Reading, Word, squeezed
from tycho.input import Presser
from tycho.input.keys import Code, UnsupportedKey, code_for
from tycho.input.plan import TAP_SECONDS, Press
from tycho.keybinds import Keybinds
from tycho.packs import Safety
from tycho.runtime.messages import Status
from tycho.runtime.planning import Refusal, Steps, plan_request
from tycho.situation import Logged

MAX_CLICKS = 2
"""The search box, then the panel's close button (ADR 3, 24 Nov 2026)."""
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 '.-]{0,39}")
"""What may be typed: letters, digits, spaces and simple punctuation, at most 40. Never
Enter in the middle, never a command (ADR 3, 23 Nov 2026)."""
_TYPED = {" ": "space", "'": "apostrophe", ".": "period", "-": "minus"}
_COMMON = frozenset({"the", "of", "on", "at", "station", "port", "city", "outpost", "base"})
"""Words in a place's name too common to tell the route line's id it's this place."""


class ScreenReader(Protocol):
    """The words on one fresh frame of the game window, read on the PC (``eyes.words``).
    Blocking. Raises ``CaptureError`` with why, if there's no frame."""

    def read(self) -> Reading: ...


class Clicker(Protocol):
    """One left click at a spot in a frame of ``window`` (``frame`` is its size), only if
    that window is in front and the frame's size. Blocking. "" if it clicked, else why not."""

    def click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str: ...


class RouteLog(Protocol):
    """What the route flow reads from the game's log (``GameLog``). Blocking."""

    def facts(self) -> frozenset[str]: ...

    def last(self, kind: str) -> Logged | None: ...

    def newest(self) -> int: ...


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
    read_every: float = 0.25


@dataclass(frozen=True, slots=True)
class Place:
    """Where to plot to: its name exactly as the map's search knows it."""

    name: str


@dataclass(slots=True)
class Plotted:
    """How the flow ended, and what reached the game."""

    status: Status
    say: str
    detail: str
    pressed: list[str] = field(default_factory=list[str])
    clicks: int = 0
    typed: bool = False


class _Stop(Exception):
    def __init__(self, status: Status, say: str, detail: str) -> None:
        super().__init__(detail)
        self.status: Status = status
        self.say, self.detail = say, detail


def plot(
    place: Place,
    flow: RouteFlow,
    *,
    keybinds: Keybinds,
    lists: Safety,
    presser: Presser,
    reader: ScreenReader,
    clicker: Clicker,
    log: RouteLog,
    cancel: threading.Event,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    name_of: Callable[[str], str | None] = lambda _id: None,
) -> Plotted:
    """Plot a route to ``place``, step by step as the module says. Blocking.
    ``name_of``: the shown name for a route line's id, if known, for "replacing"."""
    run = _Run(place, flow, keybinds, lists, presser, reader, clicker, log, cancel, sleep, clock)
    try:
        said = run.steps(name_of)
    except _Stop as stop:
        return Plotted(stop.status, stop.say, stop.detail, run.pressed, run.clicks, run.typed)
    return Plotted("done", said, f"route plotted to {place.name}", run.pressed, run.clicks, True)


@dataclass(slots=True)
class _Run:
    place: Place
    flow: RouteFlow
    keybinds: Keybinds
    lists: Safety
    presser: Presser
    reader: ScreenReader
    clicker: Clicker
    log: RouteLog
    cancel: threading.Event
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    pressed: list[str] = field(default_factory=list[str])
    clicks: int = 0
    typed: bool = False

    def steps(self, name_of: Callable[[str], str | None]) -> str:
        flow, lines, name = self.flow, self.flow.lines, self.place.name
        if not NAME.fullmatch(name):
            raise _Stop("refused", lines.bad_name.format(name=name), "not a name Tycho may type")
        presses = self._plan()
        mark = self.log.newest()
        self._press(presses[flow.nav])
        self._press(presses[flow.open_map])
        box, reading = self._map_open(mark)
        if box is None:
            raise _Stop("failed", lines.no_map, "no frame showed the map's search box")
        if not self._seated(mark):
            self._press(presses[flow.close_map])
            raise _Stop("refused", lines.on_foot, "the log shows no pilot's seat")
        self._click(reading, _middle(box), "the search box")
        self._type(name)
        self._press(presses[flow.enter])
        self._wait(flow.beat_seconds)
        reading = self._fresh()
        self._picked(reading, box)
        self._click(reading, self._close_button(reading, box), "the search panel's close button")
        replacing = ""
        if self.flow.route_set in self.log.facts():
            before = self.log.last(flow.route_line)
            old = name_of(before.name) if before is not None else None
            replacing = lines.replacing.format(name=old) if old else lines.replacing_unnamed
            self._press(presses[flow.clear])
        mark = self.log.newest()
        self._press(presses[flow.plot])
        route = self._route_line(mark)
        if route is None:
            raise _Stop("failed", lines.no_route.format(name=name), "no route line in the log")
        if not id_names(route.name, name):
            detail = f"the log's route is to {route.name}"
            raise _Stop("failed", lines.other_place.format(name=name), detail)
        self._press(presses[flow.close_map])
        done = lines.done.format(name=name)
        return f"{replacing} {done}".strip() if replacing else done

    def _plan(self) -> dict[str, Press]:
        """Every key the flow may press, through the safety gate, before any is pressed."""
        flow = self.flow
        presses: dict[str, Press] = {}
        for action in dict.fromkeys(
            (flow.nav, flow.open_map, flow.enter, flow.clear, flow.plot, flow.close_map)
        ):
            command = Command(id=f"route:{action}", say=(), actions=(action,))
            planned = plan_request((command,), self.keybinds, lists=self.lists)
            if isinstance(planned, Refusal):
                raise _Stop(planned.status, planned.detail, planned.detail)
            if not isinstance(planned, Steps) or len(planned.presses) != 1:
                raise _Stop("refused", "", f"{action} would need a confirmation")
            presses[action] = planned.presses[0][1]
        return presses

    def _press(self, press: Press) -> None:
        if self.cancel.is_set():
            raise _Stop("cancelled", "", "cancelled")
        receipt = self.presser.press(press, self.cancel)
        if receipt.sent:
            self.pressed.append(press.label or press.action)
        if not receipt.done:
            status: Status = receipt.outcome if receipt.outcome != "done" else "failed"
            raise _Stop(status, "", receipt.detail or receipt.outcome)
        self._wait(self.flow.beat_seconds)

    def _type(self, name: str) -> None:
        for letter in name.lower():
            try:
                code = code_for(_TYPED.get(letter, letter))
            except UnsupportedKey as e:  # NAME only lets through what can be typed
                raise _Stop("failed", "", str(e)) from e
            self._key(code)
            self.typed = True

    def _key(self, code: Code) -> None:
        if self.cancel.is_set():
            raise _Stop("cancelled", "", "cancelled")
        receipt = self.presser.press(Press("type", (code,), TAP_SECONDS), self.cancel)
        if not receipt.done:
            status: Status = receipt.outcome if receipt.outcome != "done" else "failed"
            raise _Stop(status, self.flow.lines.stuck, receipt.detail or receipt.outcome)

    def _wait(self, seconds: float) -> None:
        if self.cancel.wait(seconds):
            raise _Stop("cancelled", "", "cancelled")

    def _fresh(self) -> Reading:
        try:
            return self.reader.read()
        except CaptureError as e:
            raise _Stop("failed", self.flow.lines.stuck, f"no frame: {e}") from e

    def _map_open(self, mark: int) -> tuple[tuple[Word, ...] | None, Reading]:
        """The search box's words in a frame, once the log shows a seat too, or the last
        frame read when the time's up."""
        deadline = self.clock() + self.flow.open_seconds
        reading, box = Reading(0, 0), None
        while True:
            try:
                reading = self.reader.read()
                box = reading.find(self.flow.search_box)
            except CaptureError:
                box = None
            if (box is not None and self._seated(mark)) or self.clock() >= deadline:
                return box, reading
            self._wait(self.flow.read_every)

    def _seated(self, mark: int) -> bool:
        if self.flow.route_set in self.log.facts():
            return True
        line = self.log.last(self.flow.seated)
        return line is not None and line.number > mark

    def _picked(self, reading: Reading, box: tuple[Word, ...]) -> None:
        """The frame shows the map open, with the chosen place among the results."""
        lines, name = self.flow.lines, self.place.name
        under = reading.below(max(word.top + word.height for word in box))
        if reading.has(self.flow.no_results):
            raise _Stop("failed", lines.no_results.format(name=name), "the map found nothing")
        if not reading.has(self.flow.results) or not reading.has(name):
            raise _Stop("failed", lines.stuck, "the frame doesn't show the search results")
        if not under.has(name):
            raise _Stop("failed", lines.not_listed.format(name=name), "not among the results")

    def _close_button(self, reading: Reading, box: tuple[Word, ...]) -> tuple[int, int]:
        """Where the close button is from the search box, if text recognition agrees."""
        flow, height = self.flow, reading.height
        x, y = _middle(box)
        spot = (round(x + flow.close_offset[0] * height), round(y + flow.close_offset[1] * height))
        reach = flow.close_reach * height
        marks = {mark.lower() for mark in flow.close_marks}
        for word in reading.words:
            if word.text.strip().lower() in marks and _near(word.middle, spot, reach):
                return spot
        raise _Stop("failed", flow.lines.stuck, "no close button where the panel has one")

    def _click(self, reading: Reading, spot: tuple[int, int], what: str) -> None:
        if self.cancel.is_set():
            raise _Stop("cancelled", "", "cancelled")
        if self.clicks >= MAX_CLICKS:
            raise _Stop("failed", self.flow.lines.stuck, "no more clicks")
        why = self.clicker.click(reading.window, (reading.width, reading.height), *spot)
        if why:
            raise _Stop("failed", self.flow.lines.stuck, f"didn't click {what}: {why}")
        self.clicks += 1
        self.pressed.append(f"click on {what}")
        self._wait(self.flow.beat_seconds)

    def _route_line(self, mark: int) -> Logged | None:
        deadline = self.clock() + self.flow.route_seconds
        while True:
            line = self.log.last(self.flow.route_line)
            if line is not None and line.number > mark:
                return line
            if self.clock() >= deadline:
                return None
            self._wait(self.flow.read_every)


def _middle(words: tuple[Word, ...]) -> tuple[int, int]:
    left = min(word.left for word in words)
    right = max(word.right for word in words)
    top = min(word.top for word in words)
    bottom = max(word.top + word.height for word in words)
    return (left + right) // 2, (top + bottom) // 2


def _near(point: tuple[int, int], spot: tuple[int, int], reach: float) -> bool:
    return abs(point[0] - spot[0]) <= reach and abs(point[1] - spot[1]) <= reach


def id_names(route_id: str, name: str) -> bool:
    """Whether the log's route id ("levski_all-001") names the place ("Levski"): one of
    the name's own words (not ``_COMMON``, at least 3 letters) is one of the id's.
    Until Tycho reads the game's own id list, a route it can't match is reported as not
    checked, never as done (ADR 3, 24 Nov 2026)."""
    parts = set(re.split(r"[^a-z0-9]+", route_id.lower()))
    words = [w for w in squeezed(name).split() if len(w) >= 3 and w not in _COMMON]
    return bool(words) and any(word in parts for word in words)
