"""The ship look-up the brain can call (ADR 15, #54): satisfies ``brain.lookups.Lookup``.

The data loads in the background at startup (``prepare``). Until it's ready, or if
it can't be had, the look-up says so and Claude answers from memory. The tool is
offered either way, so the tools never change and the cache holds.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import cast

from tycho.brain.lookups import LookupResult, failure
from tycho.knowledge.fresh import Fresh
from tycho.knowledge.items import FRESH_SECONDS
from tycho.knowledge.ships import (
    Ambiguous,
    IsAnItem,
    NotFound,
    Ship,
    ShipLibrary,
    answer,
    rescued,
    searched,
)
from tycho.knowledge.store import ShipDataError, ensure
from tycho.knowledge.wiki import Hit, WikiError, WikiSource, search

log = logging.getLogger(__name__)

MAX_NAMES = 3
SEARCH_SECONDS = 1.5
"""How long a miss waits for the Wiki API's search (about 0.3 s): well inside the
look-up's 2.5 s, so a slow search leaves the miss as it was, not a timeout."""
SOURCE = (
    "Star Citizen Wiki API (api.star-citizen.wiki), from the game files; "
    "ships still in concept from UEX (uexcorp.space)"
)

DESCRIPTION = """\
Look up Star Citizen ships and ground vehicles in data from the current patch's \
game files: maker, role, size, crew, cargo (SCU), SCM and max speed (m/s), quantum \
range (Gm), hull and shield HP, pilot DPS, missiles, dimensions, insurance claim \
and expedite times, pledge price (USD), loaners, and where to buy or rent it in the \
game (aUEC). It also has ships still in concept (announced but not in the game, \
like the Kraken): their status says so, with fewer facts. Use it for any fact \
about a particular ship, including who makes it, even when you think you know, \
and to compare ships: pass up to three names in one call. Names can be informal \
("Connie", "the Cat", "Cutlass Black"). If a name matches several ships, the result lists them: \
pick the one the player meant, or ask. Don't use it for general questions (which \
ship is good for what, how a mechanic works), ship components or weapons, or \
commodity prices."""


class ShipLookup:
    def __init__(
        self,
        source: WikiSource,
        folder: Path,
        *,
        concepts: WikiSource | None = None,
        library: ShipLibrary | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``concepts`` is UEX, for ships still in concept. ``library`` is data
        already loaded; ``prepare`` then has nothing to do."""
        self._source = source
        self._concepts = concepts
        self._folder = folder
        self._library = library
        self._problem = "the ship data is still loading"
        # The search allows 60 requests a minute, and a name that missed once
        # misses again, so its answers are kept a while, as Items does (#221).
        self._searched: Fresh[list[Hit]] = Fresh(FRESH_SECONDS, clock)

    @property
    def library(self) -> ShipLibrary | None:
        return self._library

    @property
    def name(self) -> str:
        return "ship_lookup"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One to three ship or vehicle names, as the player said them.",
                }
            },
            "required": ["names"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        if self._library is not None:
            return
        try:
            self._library = await ensure(self._source, self._folder, concepts=self._concepts)
        except ShipDataError as e:
            self._problem = str(e)
            log.warning("Ship look-ups are off: %s", e)
        except Exception as e:  # anything else mustn't leave them "still loading" all session
            self._problem = f"the ship data couldn't be loaded ({type(e).__name__}: {e})"
            log.exception("Ship look-ups are off")

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        names = arguments.get("names")
        if not isinstance(names, list) or not names:
            return failure("pass names: a list of one to three ship names")
        library = self._library
        if library is None:
            return failure(f"{self._problem}; answer from memory, and say it may be out of date")
        asked = [str(n) for n in cast(list[object], names)[:MAX_NAMES]]
        # Off the event loop: matching a name the first time takes milliseconds
        # each, and push-to-talk must never wait for it.
        found = await asyncio.to_thread(lambda: [library.find(n) for n in asked])
        # One search per distinct miss: "Odin" and "the Odin" in one call ask once (#233).
        searches: dict[str, asyncio.Task[list[Hit] | None]] = {}
        for f in found:
            if isinstance(f, NotFound) and (query := searched(f.asked)) not in searches:
                searches[query] = asyncio.create_task(self._hits(query))
        found = await asyncio.gather(
            *(
                self._rescue(library, f, searches[searched(f.asked)])
                if isinstance(f, NotFound)
                else _now(f)
                for f in found
            )
        )
        result = {
            "source": SOURCE,
            "game_version": library.patch,
            "as_of": library.as_of,
            "stale": library.stale,
            "ships": [answer(f) for f in found],
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    async def _rescue(
        self, library: ShipLibrary, missed: NotFound, hits: Awaitable[list[Hit] | None]
    ) -> Ship | Ambiguous | NotFound | IsAnItem:
        """A miss read again with the Wiki API's search, if it answered in time;
        otherwise the miss stands."""
        if (found := await hits) is None:
            return missed
        return await asyncio.to_thread(rescued, library, missed, found)

    async def _hits(self, query: str) -> list[Hit] | None:
        """The search's answer for ``query``, kept a while; None if it's slow or fails."""
        if (kept := self._searched.get(query)) is not None:
            return kept
        try:
            hits = await asyncio.wait_for(search(self._source, query), SEARCH_SECONDS)
        except TimeoutError:
            log.debug("The Wiki API's search took over %.1f s; going on without it", SEARCH_SECONDS)
            return None
        except WikiError as e:  # never names what was searched: the player's words
            log.debug("The Wiki API's search failed (%s); going on without it", e)
            return None
        self._searched.keep(query, hits)
        return hits


async def _now[T](value: T) -> T:
    return value
