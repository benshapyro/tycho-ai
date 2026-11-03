"""The item look-up the brain can call (ADR 15): satisfies ``brain.lookups.Lookup``."""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import cast

from tycho.brain.lookups import LookupResult, failure
from tycho.knowledge.items import Found, Items, Missing, Unclear, facts
from tycho.knowledge.wiki import WikiError, WikiSource

log = logging.getLogger(__name__)

MAX_NAMES = 3
SOURCE = "Star Citizen Wiki API (api.star-citizen.wiki), from the game files"
SECONDS = 6.0
"""A name is tried in a few spellings, then its crafting recipe is fetched."""

DESCRIPTION = """\
Look up Star Citizen items by name, from the current patch's game files: FPS \
weapons (damage, rate of fire, DPS, magazine, range), armor (damage reduction), \
ship weapons (damage, DPS, range, ammo) and ship components (shields, quantum \
drives, power plants, coolers: size, grade, class, key stats), where to buy them \
in the game, and how to craft them (ingredients, time, how the blueprint is \
unlocked). Use it for any fact about a particular item, even when you think you \
know; pass up to three names to compare. Don't use it for ships (ship_lookup) or \
commodities (commodity_prices), or for general questions like "what's the best \
shield" without names."""


class ItemLookup:
    seconds = SECONDS

    def __init__(self, source: WikiSource) -> None:
        self._items = Items(source)

    @property
    def name(self) -> str:
        return "item_lookup"

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
                    "description": "One to three item names, as the player said them.",
                }
            },
            "required": ["names"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        names = arguments.get("names")
        if not isinstance(names, list) or not names:
            return failure("pass names: a list of one to three item names")
        asked = [str(n) for n in cast(list[object], names)[:MAX_NAMES]]
        try:
            found = await asyncio.gather(*(self._one(name) for name in asked))
        except WikiError as e:
            log.warning("Item look-up failed: %s", e)
            return failure(f"the Star Citizen Wiki API didn't answer ({e}); answer without it")
        body = {"source": SOURCE, "items": found}
        return LookupResult(json.dumps(body, ensure_ascii=False, separators=(",", ":")))

    async def _one(self, asked: str) -> dict[str, object]:
        match await self._items.find(asked):
            case Found(record=record):
                blueprint = await self._items.blueprint(record)
                return facts(record, blueprint)
            case Unclear(candidates=candidates):
                return {
                    "asked": asked,
                    "matches": list(candidates),
                    "note": "several items match: pick the one the player meant, or ask",
                }
            case Missing(closest=closest):
                return {
                    "asked": asked,
                    "error": "no item by that name in this patch's game files",
                    "closest": list(closest),
                }
