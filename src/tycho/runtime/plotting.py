"""Asking Tycho to plot a route on the game's map (#588). Out of ``session.py``: the
session asks ``Plotter.asked`` before the words go anywhere else, and ``answer`` does it.

Only what the player says decides it, never the model: the words start with one of the
pack's route phrases ("plot a route to"), and the rest names the place. The pack's
places part finds the name the map knows it by, and ``route.plot`` does the rest on the
key thread, through the safety gate, one step at a time (``runtime/route.py``).
"""

import asyncio
import functools
import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass

from tycho.input import Presser
from tycho.keybinds import Keybinds
from tycho.packs import Safety
from tycho.packs.routes import PlaceError, Places, Route
from tycho.runtime.messages import Progress, Status
from tycho.runtime.route import Clicker, Place, RouteLog, ScreenReader, plot
from tycho.text import normalize

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


def asked_place(text: str, phrases: Sequence[str]) -> str | None:
    """The place, if ``text`` starts with one of ``phrases`` (after ``FILLER``) and
    names one after it: "plot a route to Area 18" is "area 18". None otherwise."""
    words = normalize(text)
    while words and words[0] in FILLER:
        words.pop(0)
    for phrase in phrases:
        wanted = normalize(phrase)
        if wanted and words[: len(wanted)] == wanted:
            rest = words[len(wanted) :]
            while rest and rest[-1] in FILLER:
                rest.pop()
            return " ".join(rest) or None
    return None


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

    def asked(self, text: str) -> str | None:
        """The place the player asked for a route to, if they did."""
        return asked_place(text, self.route.say)

    async def answer(
        self,
        said: str,
        keys: Executor,
        cancel: threading.Event,
        trying: bool,
        progress: Progress,
    ) -> tuple[Status, str]:
        """Plot a route to the place ``said`` names, on the ``keys`` thread: how it
        ended and why. ``progress`` gets what was pressed and Tycho's line."""
        lines = self.route.flow.lines
        progress.commands, progress.by_brain = (ROUTE,), False
        if trying:  # setup's Try it: nothing is pressed, and a route can't be shown
            progress.say = lines.trying
            return "refused", "routes aren't plotted in Try it"
        try:
            name = await asyncio.wait_for(self.places.find(said), PLACES_SECONDS)
        except (PlaceError, TimeoutError) as e:
            log.warning("No list of places: %s", e or "it took too long")
            progress.say = lines.no_places
            return "failed", f"no list of places: {e or 'it took too long'}"
        if name is None:
            progress.say = lines.unknown_place.format(name=said)
            return "not_understood", f"no place called {said!r}"
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
        )
        plotted = await asyncio.get_running_loop().run_in_executor(keys, run)
        progress.pressed.extend(plotted.pressed)
        progress.say = plotted.say
        return plotted.status, plotted.detail
