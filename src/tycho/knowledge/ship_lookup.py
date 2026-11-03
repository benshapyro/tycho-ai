"""The ship look-up the brain can call (ADR 15, #54): satisfies ``brain.lookups.Lookup``.

The data loads in the background at startup (``prepare``). Until it's ready, or if
it can't be had, the look-up says so and Claude answers from memory. The tool is
offered either way, so the tools never change and the cache holds.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from tycho.brain.lookups import LookupResult, failure
from tycho.knowledge.ships import ShipLibrary, answer
from tycho.knowledge.store import ShipDataError, ensure
from tycho.knowledge.wiki import WikiSource

log = logging.getLogger(__name__)

MAX_NAMES = 3
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
    ) -> None:
        """``concepts`` is UEX, for ships still in concept. ``library`` is data
        already loaded; ``prepare`` then has nothing to do."""
        self._source = source
        self._concepts = concepts
        self._folder = folder
        self._library = library
        self._problem = "the ship data is still loading"

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
        result = {
            "source": SOURCE,
            "game_version": library.patch,
            "as_of": library.as_of,
            "stale": library.stale,
            # Off the event loop: matching a name the first time takes milliseconds
            # each, and push-to-talk must never wait for it.
            "ships": await asyncio.to_thread(lambda: [answer(library.find(n)) for n in asked]),
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
