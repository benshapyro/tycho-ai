"""Asking Tycho to plot a route on the game's map (#588). Out of ``session.py``: the
session asks ``Plotter.asked`` before the words go anywhere else, and ``answer`` does it.

Only what the player says decides it, never the model: the words start with one of the
pack's route phrases ("plot a route to"), and the rest names the place. The pack's
places part finds the name the map knows it by, and ``route.plot`` does the rest on the
key thread, through the safety gate, one step at a time (``runtime/route.py``).
"""

import asyncio
import functools
import json
import logging
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field

from tycho.brain.lookups import LookupResult, failure
from tycho.eyes.words import Reading
from tycho.input import Presser
from tycho.keybinds import Keybinds
from tycho.packs import Safety
from tycho.packs.routes import PlaceError, Places, Route
from tycho.runtime.messages import Progress, Status
from tycho.runtime.planning import Questions
from tycho.runtime.route import Clicker, Place, RouteLog, ScreenReader, plot
from tycho.text import normalize, similarity

log = logging.getLogger(__name__)

ROUTE = "route"
"""How plotting shows in the log and the action log."""
PLACES_SECONDS = 6.0
"""How long finding the place may take: the list is fetched the first time."""
FILLER = frozenset(
    {"tycho", "please", "hey", "ok", "okay", "can", "could", "would", "will", "you", "just"}
    | {"now", "go", "ahead", "and", "for", "me"}
)
"""Words before a route phrase, or after the place, that leave it a route request
("Tycho, plot a route to Levski please")."""


HEARD_ALIKE = 0.75
"""How alike, letter for letter, the words before the phrase's last word must be to
the phrase's, with its last two words heard right: "plotta route to" and "pona route
to" are "plot a route to" misheard (#647)."""
NOT_A_REQUEST = frozenset(
    {"best", "fastest", "quickest", "shortest", "safest", "cheapest", "longest", "nearest"}
    | {"closest", "good", "great", "trade", "trading", "mining", "cargo", "delivery"}
    | {"a", "an", "the", "any", "another", "what", "which", "how", "where", "why", "when"}
)
"""First words that make it a question about a route, not a request for one ("best
route to sell gold": "best" is one letter from "set")."""


def asked_place(text: str, phrases: Sequence[str]) -> str | None:
    """The place, if ``text`` starts with one of ``phrases`` (after ``FILLER``) and
    names one after it: "plot a route to Area 18" is "area 18". The phrase may be
    misheard, but its last word ("to") must be there, and its first word mustn't make
    it a question (``NOT_A_REQUEST``). None otherwise, and for more than a route ("to
    Hurston and spool up"), which the brain hears whole."""
    words = normalize(text)
    while words and words[0] in FILLER:
        words.pop(0)
    if not words or words[0] in NOT_A_REQUEST:
        return None
    for phrase in phrases:
        if (used := _heard(words, normalize(phrase))) is not None:
            rest = words[used:]
            while rest and rest[-1] in FILLER:
                rest.pop()
            if "and" in rest or "then" in rest:  # more than a route: the brain's (#647)
                return None
            return " ".join(rest) or None
    return None


def _heard(words: list[str], phrase: list[str]) -> int | None:
    """How many of ``words`` say ``phrase``, exactly or misheard, if they do."""
    if not phrase:
        return None
    if words[: len(phrase)] == phrase:
        return len(phrase)
    before = phrase[:-1]
    for used in range(max(2, len(phrase) - 1), len(phrase) + 2):
        # "route" or "course" must be heard right: "set cruise to max" is a command.
        if used < len(words) and words[used - 2 : used] == phrase[-2:]:
            said = "".join(words[: used - 1])
            if similarity(said, "".join(before)) >= HEARD_ALIKE:
                return used
    return None


def placed(said: str, ends: Collection[str]) -> tuple[str, str]:
    """The place and where it was said to be, when it ends in one of ``ends`` (the pack's
    ``here`` and ``away``): "levski in the next system" is ("levski", "in the next
    system"). Any other "in ..." stays part of the place ("the spaceport in lorville")
    (#661, review of #662). Compared as normalised words, as the brain may hand over
    "Levski, in the next system."."""
    words = normalize(said)
    for cut in range(1, len(words)):
        if (where := " ".join(words[cut:])) in ends:
            return " ".join(words[:cut]), where
    return said, ""


class RouteTool:
    """The brain's ``plot_route`` (#647): for a route asked in words the phrases missed.
    It only hands the place over. The session plots once the brain is done, whatever it
    decided, through the same flow, so the gate, the map's own search and the log still
    decide, and Tycho's line says how it went, never the brain's."""

    name = "plot_route"
    instant = True

    def __init__(self, description: str) -> None:
        self.description = description
        self.input_schema: Mapping[str, object] = {
            "type": "object",
            "properties": {
                "place": {
                    "type": "string",
                    "description": "The place, spelt as the game spells it (Levski, Area 18)",
                }
            },
            "required": ["place"],
            "additionalProperties": False,
        }
        self.place: str | None = None

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        place = " ".join(str(arguments.get("place", "")).split())[:60]
        if not place:
            return failure("name the place")
        self.place = place
        said = 'Tycho plots it after your reply and says how it went: reply "On it." only'
        return LookupResult(json.dumps({"handed_over": place, "reply": said}))

    def take(self) -> str | None:
        place, self.place = self.place, None
        return place


@dataclass(slots=True)
class Asking:
    """The guessed place ``answer`` last asked about (#650), for the session's confirm
    question (ADR 13), which plots it on "confirm"."""

    name: str = ""


type FailedRoute = Callable[[str, str, Reading | None], None]
"""Told the place, why and the last words read, when a route fails on the map."""


@dataclass(frozen=True, slots=True)
class Plotter:
    """What the session needs to plot a route: the pack's ``[route]`` table, its places,
    and the game's window, input and log."""

    route: Route
    places: Places
    reader: ScreenReader
    clicker: Clicker
    log: RouteLog
    keybinds: Callable[[], Keybinds]
    lists: Safety
    presser: Presser
    tool: RouteTool | None = None
    """The brain's way to ask for a route, if the pack gives it one."""
    asking: Asking = field(default_factory=Asking)
    address: Callable[[], str] = lambda: ""
    """What to call the player, at the end of a question (#15)."""
    record: FailedRoute | None = None
    """Told the place, why and the last words read when a route fails on the map, with
    ``TYCHO_ROUTE_RECORD`` on (``eyes.record.FrameRecord.failed``)."""

    def asked(self, text: str) -> str | None:
        """The place the player asked for a route to, if they did. A new request:
        anything the brain handed over for an earlier one is dropped."""
        if self.tool is not None:
            self.tool.take()
        return asked_place(text, self.route.say)

    def handed(self, plain: bool) -> str | None:
        """The place the brain handed over in this request, if it did and ``plain``: the
        brain looked at nothing, searched nothing and looked nothing up first, so the
        place is the player's words. Otherwise it's dropped: nothing presses after a
        look (ADR 14), a web search (ADR 15) or a search of past talk."""
        place = self.tool.take() if self.tool is not None else None
        return place if plain else None

    async def answer(
        self,
        said: str,
        keys: Executor,
        cancel: threading.Event,
        trying: bool,
        progress: Progress,
        sure: bool = False,
    ) -> tuple[Status, str]:
        """Plot a route to the place ``said`` names, on the ``keys`` thread: how it
        ended and why. ``progress`` gets what was pressed and Tycho's line. Unless
        ``sure`` (confirmed), a guessed place ends "asking" (#650): ``progress.question``
        asks about it (ending with the player's name, #15), ``asking.name`` names
        it, and nothing is pressed."""
        lines = self.route.flow.lines
        progress.commands, progress.by_brain = (ROUTE,), False
        if trying:  # setup's Try it: nothing is pressed, and a route can't be shown
            progress.say = lines.trying
            return "refused", "routes aren't plotted in Try it"
        if self.lists.keys_off:  # the gate's reason, with nothing looked up (review of #643)
            progress.say = self.lists.keys_off
            return "refused", self.lists.keys_off
        said, where = placed(said, self.route.here | self.route.away)
        if where in self.route.away:  # e.g. the map only searches the player's system
            progress.say = lines.elsewhere.format(name=said)
            return "refused", f"{said!r} is {where}"
        try:
            name = await asyncio.wait_for(self.places.find(said), PLACES_SECONDS)
        except (PlaceError, TimeoutError) as e:
            why = str(e) or "it took too long"
            log.warning("No list of places: %s", why)
            progress.say = lines.no_places
            return "failed", f"no list of places: {why}"
        if name is None:
            progress.say = lines.unknown_place.format(name=said)
            return "not_understood", f"no place called {said!r}"
        if not sure and self.places.guessed(said, name):  # #650: ask first
            asking = (lines.confirm.format(name=name).rstrip("?"),)  # "? Say confirm." follows
            self.asking.name, progress.question = (
                name,
                Questions((), asking).question(self.address()),
            )
            return "asking", f"{said!r} may be {name}"
        log.info("Plotting a route to %s", name)
        run = functools.partial(
            plot,
            Place(name),
            self.route.flow,
            keybinds=self.keybinds(),
            lists=self.lists,
            presser=self.presser,
            reader=self.reader,
            clicker=self.clicker,
            log=self.log,
            cancel=cancel,
            name_of=self.places.name_of,
            record=functools.partial(self.record, name) if self.record is not None else None,
        )
        plotted = await asyncio.get_running_loop().run_in_executor(keys, run)
        progress.pressed.extend(plotted.pressed)
        progress.say = plotted.say
        return plotted.status, plotted.detail
