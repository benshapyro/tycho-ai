"""Plotting a route on the game's map by voice (#588, ADR 3's three amendments for it,
ADR 14's "a local check before a press").

The flow, as the pack declares it (``RouteFlow``):

1. set the mode that takes the weapons offline (``nav``), so a click that misses the map
   can't fire;
2. open the map (``open_map``) and wait, at most ``open_seconds``, for a frame that shows
   its search box, and for the log to show the player is in a pilot's seat: a route
   already set (``route_set``), or the map's "no route" line (``seated``) since step 1;
3. once the box sits still in two looks ``settle_seconds`` apart (the map has finished
   opening), click it, in the later frame (the first click), and wait ``click_seconds``;
4. check the map is still open: the box's empty words, or the list a box with the keys
   shows under it (it drops them, 26 Nov 2026); else nothing is typed. Type the name,
   letter by letter: after the first, the empty words must be gone and a letter on the
   box's line or the RECENT list gone, and after the last, the box's own line must
   show the name, before Enter (``enter``) is pressed (#654: letters that didn't land
   could reach the game);
5. read a fresh frame: the RESULTS header under the box, and the chosen place under it
   in the panel's column (a RECENT list doesn't count, #654);
   or, as Enter can open it straight away (Ben's PC try, 26 Nov 2026), the place's own
   panel: its name as a line of its own just under the box; or, when Enter emptied the
   box (its empty words back), the place under RECENT (Orison, 26 Nov 2026);
6. the second, last click, to take the keys from the box: on that place's name (Ben: it
   only needs a click outside the search bar, which also leaves the place chosen), else
   on the results panel's close button, read on the box's line or found by the pack's
   offset, and only if text recognition agrees something is there;
7. clear the route (``clear``) if one is set, then plot (``plot``);
8. wait for the log's route line, and check its id names the chosen place;
9. close the map (``close_map``).

Every key goes through the safety gate (``plan_request``), so with key actions off, or a
key that clashes with something dangerous, nothing is pressed. Every press and click
checks the game window is in front. Any step that fails stops the flow there: nothing
more is pressed, and the map is left open for the player (except on foot, where it's
closed again, having shown nothing). One request types one name once, and never tries
again on its own. With ``record`` (``TYCHO_ROUTE_RECORD``, a review tool), a stop after
the map was opened hands why, and the last check's words, to it (ADR 14, 25 Nov 2026).

Blocking: the session runs ``plot`` on the key thread. A cancel (push-to-talk pressed
again) stops it between steps.
"""

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from tycho.commands import Command
from tycho.eyes import CaptureError
from tycho.eyes.words import Reading, Word, squeezed
from tycho.input import Presser
from tycho.input.keys import Code, UnsupportedKey, code_for
from tycho.input.plan import TAP_SECONDS, Press
from tycho.keybinds import Keybinds
from tycho.packs import Safety
from tycho.packs.routes import RouteFlow
from tycho.runtime import mapwords
from tycho.runtime.mapwords import column, middle, near, row, shows
from tycho.runtime.messages import Status
from tycho.runtime.planning import Refusal, Steps, plan_request
from tycho.safety import is_confirm_grade
from tycho.situation import Logged

log = logging.getLogger(__name__)

MAX_CLICKS = 2
"""The search box, then the place's name on its own panel or else the search panel's
close button (ADR 3, 24 and 26 Nov 2026)."""
SETTLE_LOOKS = 4
"""Looks, ``settle_seconds`` apart, for the search box to stop moving as the map opens."""
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
    record: Callable[[str, Reading | None], None] | None = None,
) -> Plotted:
    """Plot a route to ``place``, step by step as the module says. Blocking.
    ``name_of``: the shown name for a route line's id, if known, for "replacing".
    ``record``: told why, and the last words read, when the flow fails on the map."""
    run = _Run(place, flow, keybinds, lists, presser, reader, clicker, log, cancel, sleep, clock)
    try:
        said = run.steps(name_of)
    except _Stop as stop:
        if record is not None and stop.status == "failed" and run.opened:
            try:
                record(stop.detail, run.last)
            except Exception:  # a review tool never changes what the player hears
                logging.getLogger(__name__).debug("The route record failed", exc_info=True)
        return Plotted(stop.status, stop.say, stop.detail, run.pressed, run.clicks, run.typed)
    finally:
        if isinstance(reader, Warm):  # one route's reading process ends with it
            reader.finish()
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
    opened: bool = False
    """The map's key was pressed: a failure from here on is the map's to record."""
    last: Reading | None = None
    """The last frame's words, for the record."""

    def steps(self, name_of: Callable[[str], str | None]) -> str:
        flow, lines, name = self.flow, self.flow.lines, self.place.name
        if not NAME.fullmatch(name):
            raise _Stop("refused", lines.bad_name.format(name=name), "not a name Tycho may type")
        presses = self._plan()
        if risky := self._risky_letter(name):
            detail = f"{risky} is on a key in the name, so it isn't typed"
            raise _Stop("refused", lines.bad_name.format(name=name), detail)
        if isinstance(self.reader, Warm):  # starts reading while the map opens (#661)
            self.reader.warm()
        mark = self.log.newest()
        self._press(presses[flow.nav])
        self._press(presses[flow.open_map])
        self.opened = True
        box, reading = self._map_open(mark)
        if box is None:
            raise _Stop("failed", lines.no_map, "no frame showed the map's search box")
        if not self._seated(mark):
            self._press(presses[flow.close_map])
            raise _Stop("refused", lines.on_foot, "the log shows no pilot's seat")
        box, reading = self._settled(box)
        self._click(reading, middle(box), "the search box")
        after = self._fresh()  # a box with the keys drops its empty words (26 Nov 2026)
        if not after.has(flow.search_box) and not self._listed(after, box):
            raise _Stop("failed", lines.stuck, "the map closed before typing")
        self._wait(flow.click_seconds)  # the box takes keys a moment after the click (#654)
        self._type(name[:1])
        self.pressed.append(f"typed {name[:1]}")  # the log shows it, even if the game took it
        self._wait(flow.beat_seconds)
        now = self._fresh()
        if now.has(flow.search_box) or not self._took(after, now, box, squeezed(name[:1])):
            raise _Stop("failed", lines.not_typed, "the search box didn't take the first letter")
        self._type(name[1:])
        self._wait(flow.beat_seconds)
        if not shows(row(self._fresh(), box), name):
            raise _Stop("failed", lines.not_typed, "the search box doesn't show the name")
        self.pressed[-1] = f"typed {name}"
        self._press(presses[flow.enter])
        self._wait(flow.beat_seconds)
        reading = self._fresh()
        if (title := self._detail(reading, box)) is not None:  # the place's panel opened
            self._click(reading, middle(title), "the place's name in the panel")
        elif (pick := self._recent(reading, box)) is not None:  # Enter emptied the box
            self._click(reading, middle(pick), "the place's name under recent")
        else:
            self._picked(reading, box)
            self._click(
                reading, self._close_button(reading, box), "the search panel's close button"
            )
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
                say = self.flow.lines.no_key if planned.status == "no_key" else planned.detail
                raise _Stop(planned.status, say, planned.detail)
            if not isinstance(planned, Steps) or len(planned.presses) != 1:
                raise _Stop("refused", "", f"{action} would need a confirmation")
            presses[action] = planned.presses[0][1]
        return presses

    def _risky_letter(self, name: str) -> str:
        """A confirm-grade action on one of the name's keys alone, if any: if the box
        didn't take the keys after all, a letter must not press it (review of #636)."""
        letters = {_TYPED.get(letter, letter) for letter in name.lower()}
        for action in self.keybinds:
            risky = is_confirm_grade(action.name, self.lists) or (
                bool(action.label) and is_confirm_grade(action.label, self.lists)
            )
            if risky and any(k.key in letters and not k.modifiers for k in action.inputs):
                return action.label or action.name
        return ""

    def _press(self, press: Press) -> None:
        if self.cancel.is_set():
            raise _Stop("cancelled", "", "cancelled")
        receipt = self.presser.press(press, self.cancel)
        if receipt.sent:
            self.pressed.append(press.label or press.action)
        if not receipt.done:
            status: Status = receipt.outcome if receipt.outcome != "done" else "failed"
            said = receipt.detail if status == "modifier_held" else ""  # "Let go of Ctrl" (#645)
            raise _Stop(status, said, receipt.detail or receipt.outcome)
        self._wait(self.flow.beat_seconds)

    def _type(self, name: str) -> None:
        for letter in name.lower():
            try:
                code = code_for(_TYPED.get(letter, letter))
            except UnsupportedKey as e:  # NAME only lets through what can be typed
                raise _Stop("failed", "", str(e)) from e
            self._key(code)
            log.debug("Typed %r into the map's search", letter)
            self.typed = True

    def _key(self, code: Code) -> None:
        if self.cancel.is_set():
            raise _Stop("cancelled", "", "cancelled")
        receipt = self.presser.press(Press("type", (code,), TAP_SECONDS), self.cancel)
        if not receipt.done:
            status: Status = receipt.outcome if receipt.outcome != "done" else "failed"
            said = receipt.detail if status == "modifier_held" else self.flow.lines.stuck
            raise _Stop(status, said, receipt.detail or receipt.outcome)

    def _wait(self, seconds: float) -> None:
        if self.cancel.wait(seconds):
            raise _Stop("cancelled", "", "cancelled")

    def _fresh(self) -> Reading:
        try:
            self.last = self.reader.read()
            return self.last
        except CaptureError as e:
            raise _Stop("failed", self.flow.lines.stuck, f"no frame: {e}") from e

    def _map_open(self, mark: int) -> tuple[tuple[Word, ...] | None, Reading]:
        """The search box's words in a frame, once the log shows a seat too, or the last
        frame read when the time's up."""
        deadline = self.clock() + self.flow.open_seconds
        reading, box, looks = Reading(0, 0), None, 0
        while True:
            looks += 1
            try:
                reading = self.last = self.reader.read()
                box = reading.find(self.flow.search_box)
            except CaptureError as e:
                log.info("Map check, look %d: %s", looks, e)
                box = None
            if (box is not None and self._seated(mark)) or self.clock() >= deadline:
                if box is None:  # how many looks it had, for the log (#661)
                    log.info("No search box in %d looks over %.1f s", looks, self.flow.open_seconds)
                return box, reading
            self._wait(self.flow.read_every)

    def _settled(self, box: tuple[Word, ...]) -> tuple[tuple[Word, ...], Reading]:
        """The search box in a frame once the map has finished opening: in the same place
        as in the look ``settle_seconds`` before (Ben's PC try, 25 Nov 2026: a click
        straight away, while the map still opened, didn't take)."""
        flow = self.flow
        for _ in range(SETTLE_LOOKS):
            self._wait(flow.settle_seconds)
            try:
                reading = self.last = self.reader.read()
            except CaptureError as e:  # one bad look is one of the looks (review of #670)
                log.info("Map check, settling: %s", e)
                continue
            now = reading.find(flow.search_box)
            if now is None:  # missed over the moving map, or gone: look again
                continue
            if near(middle(now), middle(box), _STILL * reading.height):
                return now, reading
            box = now
        raise _Stop("failed", flow.lines.no_map, "the map's search box didn't sit still")

    def _seated(self, mark: int) -> bool:
        if self.flow.route_set in self.log.facts():
            return True
        line = self.log.last(self.flow.seated)
        return line is not None and line.number > mark

    def _picked(self, reading: Reading, box: tuple[Word, ...]) -> None:
        """The frame shows the map open, with the chosen place among the results."""
        lines, name = self.flow.lines, self.place.name
        under = column(reading.below(max(word.top + word.height for word in box)), box, reading)
        if reading.has(self.flow.no_results):
            raise _Stop("failed", lines.no_results.format(name=name), "the map found nothing")
        header = under.find(self.flow.results)
        if header is None:
            raise _Stop("failed", lines.stuck, "the frame doesn't show the search results")
        listed = under.below(max(word.top + word.height for word in header))
        if not shows(listed, name):  # under RESULTS, not a RECENT list or a marker (#654)
            raise _Stop("failed", lines.not_listed.format(name=name), "not among the results")

    def _marks(self, reading: Reading, box: tuple[Word, ...]) -> list[Word]:
        """The close marks ("x") on the search box's line, right of where its words were:
        the box's own x, there once the box has the keys (Ben's PC try, 26 Nov 2026)."""
        marks = {mark.lower() for mark in self.flow.close_marks}
        right = max(word.right for word in box)
        line = row(reading, box).words
        return [w for w in line if w.text.strip().lower() in marks and w.left > right]

    def _listed(self, reading: Reading, box: tuple[Word, ...]) -> bool:
        """The panel's list is open under the box: "RECENT" or "ALL RESULTS" in its column,
        as once the box has the keys (Ben's PC try, 26 Nov 2026)."""
        close = mapwords.close_under(reading, box, _LISTED)
        return any(word and close.has(word) for word in (self.flow.recent, self.flow.results))

    def _detail(self, reading: Reading, box: tuple[Word, ...]) -> tuple[Word, ...] | None:
        """The chosen place's name as a line of its own just under the box: the panel
        Enter opens for one result (26 Nov 2026), not a row of a results list, nor its
        name inside a description."""
        if self._listed(reading, box):  # a list's rows aren't the panel
            return None
        for line in mapwords.close_under(reading, box, _LISTED).lines():
            if mapwords.alike(" ".join(w.text for w in line), self.place.name):
                return line
        return None

    def _recent(self, reading: Reading, box: tuple[Word, ...]) -> tuple[Word, ...] | None:
        """The place under RECENT, when Enter picked it and emptied the box: its empty
        words back, no filtered list (Ben's Orison try, 26 Nov 2026)."""
        if not self.flow.recent or not row(reading, box).has(self.flow.search_box):
            return None
        under = column(reading.below(max(w.top + w.height for w in box)), box, reading)
        return mapwords.under_header(under, self.flow.recent, self.flow.results, self.place.name)

    def _took(self, before: Reading, after: Reading, box: tuple[Word, ...], first: str) -> bool:
        """The first letter landed: a new word on the box's line that starts with it (not
        a caret read as "I", review of #672), or the RECENT list gone while the panel
        still shows (text recognition may not read one letter)."""
        marks = {mark.lower() for mark in self.flow.close_marks}
        was = {squeezed(w.text) for w in row(before, box).words}
        for word in row(after, box).words:
            text = squeezed(word.text)
            if text and text not in marks and text not in was and text.startswith(first):
                return True
        recent = self.flow.recent
        gone = bool(recent) and before.has(recent) and not after.has(recent)
        return gone and self._listed(after, box)

    def _close_button(self, reading: Reading, box: tuple[Word, ...]) -> tuple[int, int]:
        """The close button: the x read on the box's line, or where the pack's offset puts
        it from the box, if text recognition agrees (the offset was measured at 2560x1440;
        on a 5120x2160 screen the x sat nearer, 26 Nov 2026)."""
        if marks := self._marks(reading, box):
            return min(marks, key=lambda word: word.left).middle
        flow, height = self.flow, reading.height
        x, y = middle(box)
        spot = (round(x + flow.close_offset[0] * height), round(y + flow.close_offset[1] * height))
        reach = flow.close_reach * height
        marks_said = {mark.lower() for mark in flow.close_marks}
        for word in reading.words:
            if word.text.strip().lower() in marks_said and near(word.middle, spot, reach):
                return spot
        raise _Stop("failed", flow.lines.stuck, "no close button where the panel has one")

    def _click(self, reading: Reading, spot: tuple[int, int], what: str) -> None:
        if self.cancel.is_set():
            raise _Stop("cancelled", "", "cancelled")
        if self.clicks >= MAX_CLICKS:
            raise _Stop("failed", self.flow.lines.stuck, "no more clicks")
        try:
            why = self.clicker.click(reading.window, (reading.width, reading.height), *spot)
        except OSError as e:  # Windows refused the input
            raise _Stop("failed", self.flow.lines.stuck, f"didn't click {what}: {e}") from e
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


_LISTED = 0.15
"""How far under the box, as a fraction of the frame's height, its list starts."""
_STILL = 0.005
"""How far, as a fraction of the frame's height, the box may move and still be settled:
a few pixels, as text recognition reads the same words a little differently."""


def id_names(route_id: str, name: str) -> bool:
    """Whether the log's route id ("levski_all-001") names the place ("Levski"): one of
    the name's own words (not ``_COMMON``, at least 3 letters) is one of the id's.
    Until Tycho reads the game's own id list, a route it can't match is reported as not
    checked, never as done (ADR 3, 24 Nov 2026)."""
    parts = set(re.split(r"[^a-z0-9]+", route_id.lower()))
    words = [w for w in squeezed(name).split() if len(w) >= 3 and w not in _COMMON]
    return bool(words) and any(word in parts for word in words)
