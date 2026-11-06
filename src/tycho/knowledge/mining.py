"""Where to mine a resource (#151), from the game files via the Star Citizen Wiki API:
satisfies ``brain.lookups.Lookup``.

The API lists the locations that have a resource (``/api/locations?filter[resource]=``),
and each location's mining groups with their chances (``?include=resources``). The
filter needs the exact name ("Quantainium (Raw)", "Lindinium (Ore)"), so the name as
said is tried in those forms. Quantainium is in 66 places, so only a few are looked
at closely: those in the place the player named, if any, else the first few.
"""

import asyncio
import difflib
import json
import logging
import time
from collections.abc import Mapping
from typing import Any, cast

from tycho.brain.lookups import LookupResult, failure
from tycho.knowledge.wiki import WikiError, WikiSource
from tycho.text import normalize

log = logging.getLogger(__name__)

SOURCE = "Star Citizen Wiki API (api.star-citizen.wiki), from the game files"
SECONDS = 5.0
"""The list, then every location at once: about 1.5 s measured, cached after."""
DETAILED = 100
"""Locations looked at closely for their chances: all of them (66 at most measured,
0.7 s at once), so the best are found, not the first in the alphabet."""
SHOWN = 6
KEEP_SECONDS = 3600.0
"""Mining data changes only with a patch: what was fetched is kept for an hour."""
FORMS = ("{} (Raw)", "{} (Ore)", "{}")
"""How the game names a mineable: ship ores are "(Raw)", most others "(Ore)"."""

DESCRIPTION = """\
Where to mine a resource in Star Citizen, from the current patch's game files: the \
moons, planets and asteroid fields that have it, how it's mined there (ship, ground \
vehicle or hand mining), and the chance of finding it. Pass the resource as the \
player said it ("quantanium", "lindinium"), and a place if they named one (a system, \
planet or moon), to keep to it. Use it for "where do I mine X" and "what's on \
Daymar"-style questions about mining; for selling ore use commodity_prices. In the \
answer, credit the Star Citizen Wiki once, in a few words. A result with not_found: \
say briefly what wasn't found."""


class MiningLookup:
    seconds = SECONDS

    def __init__(self, source: WikiSource, clock: Any = time.monotonic) -> None:
        self._source = source
        self._clock = clock
        self._kept: dict[str, tuple[float, Any]] = {}
        self._names: dict[str, list[str]] = {}
        """The game's commodity names by their plain form ("quantainium": ["Quantainium
        (Raw)"]), for names said or spelt a little wrong. Empty until prepared."""

    @property
    def name(self) -> str:
        return "mining_lookup"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "description": "As the player said it."},
                "place": {
                    "type": "string",
                    "description": "A system, planet or moon, if they named one; otherwise empty.",
                },
            },
            "required": ["resource", "place"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        """The game's commodity names, once. Never raises: without them, a name is
        tried as said, in the game's forms."""
        try:
            pages = await asyncio.gather(
                *(
                    self._get("/api/commodities", {"page[size]": 200, "page[number]": n})
                    for n in (1, 2)
                )
            )
        except WikiError as e:
            log.warning("Mining names didn't load: %s", e)
            return
        for page in pages:
            for record in cast(list[dict[str, Any]], page.get("data") or []):
                if name := str(record.get("name") or ""):
                    self._names.setdefault(_key(name), []).append(name)

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        said = " ".join(str(arguments.get("resource") or "").split())
        place = " ".join(str(arguments.get("place") or "").split())
        if not said:
            return self._result({"not_found": "no resource: ask what they want to mine"})
        try:
            resource, locations = await self._find(said)
            if not locations:
                return self._result({"not_found": f"no location in the game files has {said!r}"})
            if place:
                inside = [loc for loc in locations if _in(loc, place)]
                if not inside:
                    systems = sorted({str(loc.get("system") or "") for loc in locations} - {""})
                    return self._result(
                        {
                            "resource": resource,
                            "not_found": f"{resource} isn't found in {place}",
                            "found_in_systems": systems,
                        }
                    )
                locations = inside
            details = await asyncio.gather(
                *(
                    self._resources(str(loc["slug"]))
                    for loc in locations[:DETAILED]
                    if loc.get("slug")
                )
            )
        except WikiError as e:
            log.warning("Mining look-up failed: %s", e)
            return failure(f"the Star Citizen Wiki API didn't answer ({e}); answer without it")
        places = [
            _place(loc, groups, resource)
            for loc, groups in zip(locations[:DETAILED], details, strict=False)
        ]
        places.sort(key=lambda p: -float(cast(float, p.get("chance_percent") or 0)))
        body: dict[str, object] = {
            "resource": resource,
            "found_in": len(locations),
            "places": places[:SHOWN],
        }
        if len(locations) > DETAILED:
            body["note"] = f"{len(locations)} places have it; the chances are for {DETAILED}"
        return self._result(body)

    async def _find(self, said: str) -> tuple[str, list[dict[str, Any]]]:
        """The resource's name in the game files, and the locations that have it."""
        plain = _plain(said)
        names = self._known(plain) or [form.format(plain) for form in FORMS]
        found = await asyncio.gather(*(self._locations(n) for n in names))
        for name, locations in zip(names, found, strict=True):
            if locations:
                return name, locations
        return plain, []

    def _known(self, plain: str) -> list[str]:
        """The game's names for what was said, nearest spelling first ("quantanium" is
        Quantainium (Raw))."""
        key = _key(plain)
        if key in self._names:
            return self._names[key]
        close = difflib.get_close_matches(key, list(self._names), n=1, cutoff=0.88)
        return self._names[close[0]] if close else []

    async def _locations(self, resource: str) -> list[dict[str, Any]]:
        data = await self._get("/api/locations", {"filter[resource]": resource, "page[size]": 100})
        return [loc for loc in cast(list[Any], data.get("data") or []) if isinstance(loc, dict)]

    async def _resources(self, slug: str) -> list[dict[str, Any]]:
        data = await self._get(f"/api/locations/{slug}", {"include": "resources"})
        record = cast(dict[str, Any], data.get("data") or {})
        return [g for g in cast(list[Any], record.get("resources") or []) if isinstance(g, dict)]

    async def _get(self, path: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        key = f"{path}?{sorted(params.items())}"
        if (hit := self._kept.get(key)) is not None and self._clock() - hit[0] < KEEP_SECONDS:
            return cast(dict[str, Any], hit[1])
        data = await self._source.get(path, params)
        if not isinstance(data, dict):
            raise WikiError(f"{path}: not an object")
        self._kept[key] = (self._clock(), data)
        return cast(dict[str, Any], data)

    def _result(self, body: Mapping[str, object]) -> LookupResult:
        return LookupResult(
            json.dumps({"source": SOURCE, **body}, ensure_ascii=False, separators=(",", ":"))
        )


def _key(name: str) -> str:
    """A name without the game's ore marks, for matching: "Raw Ice" and "Quantainium
    (Raw)" are "ice" and "quantainium"."""
    for mark in ("(Raw)", "(Ore)"):
        name = name.replace(mark, "")
    words = [w for w in name.lower().split() if w not in {"raw", "ore"}]
    return " ".join(words)


def _plain(said: str) -> str:
    """ "raw quantainium ore" -> "Quantainium": the forms add the game's marks back."""
    words = [w for w in said.split() if w.lower() not in {"raw", "ore", "unrefined", "refined"}]
    return " ".join(w.capitalize() for w in words) or said.capitalize()


def _in(location: Mapping[str, Any], place: str) -> bool:
    """Whether the location is the place, is on it, or is in its system."""
    key = "".join(normalize(place.replace("system", "")))
    names = [
        location.get("name"),
        cast(dict[str, Any], location.get("parent") or {}).get("name"),
        str(location.get("system") or "").replace("System", ""),
    ]
    return any(key and key == "".join(normalize(str(n))) for n in names if n)


def _place(
    location: Mapping[str, Any], groups: list[dict[str, Any]], resource: str
) -> dict[str, object]:
    """A location as it's said, with how the resource is mined there and its chance:
    the group's chance times the resource's share of it, at its best."""
    mined: dict[str, float] = {}
    for group in groups:
        for entry in cast(list[Any], group.get("resources") or []):
            for material in cast(list[Any], cast(dict[str, Any], entry).get("materials") or []):
                m = cast(dict[str, Any], material)
                if m.get("name") != resource:
                    continue
                chance = float(m.get("group_probability") or 0) * float(
                    m.get("relative_probability") or 0
                )
                how = str(group.get("mining_type") or "mining")
                mined[how] = max(mined.get(how, 0.0), chance)
    parent = cast(dict[str, Any], location.get("parent") or {}).get("name")
    system = str(location.get("system") or "").replace(" System", "")
    out: dict[str, object] = {
        "name": location.get("name"),
        "kind": cast(dict[str, Any], location.get("type") or {}).get("name"),
        "around": ", ".join(dict.fromkeys(v for v in (str(parent or ""), system) if v)),
        "mining": sorted(mined, key=lambda k: -mined[k]),
    }
    if mined:
        out["chance_percent"] = round(100 * max(mined.values()), 1)
    return out
