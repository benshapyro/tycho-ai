"""Where to mine a resource (#151), from the game files via the Star Citizen Wiki API:
satisfies ``brain.lookups.Lookup``.

The API lists the locations that have a resource (``/api/locations?filter[resource]=``),
and each location's mining groups with their chances (``?include=resources``). The
filter needs the exact name ("Quantainium (Raw)", "Lindinium (Ore)"), so the name as
said is matched to the game's own names first. Every location found is looked at
closely, at once (66 for Quantainium, about 0.7 s), so the best are found; a place
the player named keeps it to that place.

A recipe asks for several materials at once (#667): their fetches are shared, so the
same location is fetched once, and a fetch carries on when the look-up that started
it runs out of time, so the next ask finds it kept. Locations still unanswered at
``DETAILS_SECONDS`` are listed without their chance rather than the look-up timing out.
"""

import asyncio
import difflib
import json
import logging
import time
from collections.abc import Coroutine, Mapping
from typing import Any, cast

from tycho.brain.lookups import LookupResult, failure
from tycho.games.star_citizen.wiki import WikiError, WikiSource
from tycho.text import normalize

log = logging.getLogger(__name__)

SOURCE = "Star Citizen Wiki API (api.star-citizen.wiki), from the game files"
SECONDS = 3.0
"""The list, then every location at once: about 1.5 s measured, cached after (at most
1.0 s once loaded, 8 November 2026, #150)."""
DETAILED = 100
"""Locations looked at closely for their chances: all of them (66 at most measured,
0.7 s at once), so the best are found, not the first in the alphabet."""
SHOWN = 6
PAGE = 100
"""Locations asked for in one request; more than this and the answer says so."""
DETAILS_SECONDS = 2.2
"""From the look-up's start, how long it waits for locations' chances before it
answers with what it has, inside ``SECONDS``. Three materials at once on a cold start
ran past ``SECONDS`` on the player's PC, and the recipe lost every mining spot (#667)."""
KEEP_SECONDS = 3600.0
"""Mining data changes only with a patch: what was fetched is kept for an hour."""
FORMS = ("{} (Raw)", "{} (Ore)", "{} (R)", "{}")
"""How the game names a mineable: ship ores are "(Raw)", most others "(Ore)", and
Hephaestanite "(R)" (4.10.3, #597)."""

DESCRIPTION = """\
Where to mine a resource in Star Citizen, from the current patch's game files: the \
moons, planets and asteroid fields that have it, how it's mined there (ship, ground \
vehicle or hand mining), and the chance of finding it. Pass the resource as the \
player said it ("quantanium", "lindinium"), and a place to keep to: one they named (a \
system, planet or moon), else the system they're in now if known (their words, then \
the request's log line; never a home base), else empty for every system. Use it \
for "where do I mine X" and "is there X on Daymar"; it needs a resource. For \
selling ore use commodity_prices. In the \
answer, credit the Star Citizen Wiki once, in a few words. A result with not_found: \
say briefly what wasn't found."""


class MiningLookup:
    seconds = SECONDS

    def __init__(self, source: WikiSource, clock: Any = time.monotonic) -> None:
        self._source = source
        self._clock = clock
        self._kept: dict[str, tuple[float, Any]] = {}
        self._fetching: dict[str, asyncio.Task[dict[str, Any]]] = {}
        """Fetches under way, by what was asked, shared by every look-up that needs one
        (#667): three materials at once share their locations."""
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
                    "description": "A system, planet or moon: named, else theirs now; else empty.",
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
            for record in cast(list[Any], page.get("data") or []):
                if not isinstance(record, dict):
                    continue
                if name := str(cast(dict[str, Any], record).get("name") or ""):
                    self._names.setdefault(_key(name), []).append(name)

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        by = asyncio.get_running_loop().time() + DETAILS_SECONDS
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
            looked = [loc for loc in locations if loc.get("slug")][:DETAILED]
            answers = await _by(by, [self._resources(str(loc["slug"])) for loc in looked])
        except WikiError as e:
            log.warning("Mining look-up failed: %s", e)
            return failure(f"the Star Citizen Wiki API didn't answer ({e}); answer without it")
        late = sum(isinstance(a, TimeoutError) for a in answers)
        missing = sum(isinstance(a, BaseException) for a in answers)
        if all(isinstance(a, BaseException) for a in answers) and answers and not late:
            first = next(a for a in answers if isinstance(a, BaseException))
            log.warning("Mining look-up failed: %s", first)
            return failure(f"the Star Citizen Wiki API didn't answer ({first}); answer without it")
        # A location the Wiki didn't answer for (a rate limit, say) is listed without its
        # chance, rather than the whole answer failing.
        details = [[] if isinstance(a, BaseException) else a for a in answers]
        places = [
            _place(loc, groups, resource) for loc, groups in zip(looked, details, strict=True)
        ]
        places.sort(key=lambda p: -float(cast(float, p.get("chance_percent") or 0)))
        body: dict[str, object] = {
            "resource": resource,
            "found_in": len(locations),
            "places": places[:SHOWN],
        }
        if late:
            body["note"] = (
                f"the Wiki was slow: chances for {len(looked) - missing} of {len(looked)} "
                "places; the others are listed without one"
            )
        elif len(locations) >= PAGE:
            body["note"] = f"at least {PAGE} places have it; these are the best of those"
        elif len(locations) > DETAILED:
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
        data = await self._get("/api/locations", {"filter[resource]": resource, "page[size]": PAGE})
        return [loc for loc in cast(list[Any], data.get("data") or []) if isinstance(loc, dict)]

    async def _resources(self, slug: str) -> list[dict[str, Any]]:
        data = await self._get(f"/api/locations/{slug}", {"include": "resources"})
        record = cast(dict[str, Any], data.get("data") or {})
        return [g for g in cast(list[Any], record.get("resources") or []) if isinstance(g, dict)]

    async def _get(self, path: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        key = f"{path}?{sorted(params.items())}"
        if (hit := self._kept.get(key)) is not None and self._clock() - hit[0] < KEEP_SECONDS:
            return cast(dict[str, Any], hit[1])
        fetching = self._fetching.get(key)
        if fetching is None or fetching.done():
            fetching = asyncio.create_task(self._fetch(key, path, params))
            self._fetching[key] = fetching
            fetching.add_done_callback(lambda done: self._fetched(key, done))
        # Shielded: a look-up out of time stops waiting, but the fetch carries on for
        # the others sharing it, and is kept for the next ask (#667).
        return await asyncio.shield(fetching)

    async def _fetch(self, key: str, path: str, params: Mapping[str, str | int]) -> dict[str, Any]:
        data = await self._source.get(path, params)
        if not isinstance(data, dict):
            raise WikiError(f"{path}: not an object")
        self._kept[key] = (self._clock(), data)
        return cast(dict[str, Any], data)

    def _fetched(self, key: str, done: asyncio.Task[dict[str, Any]]) -> None:
        if self._fetching.get(key) is done:
            del self._fetching[key]
        if not done.cancelled():
            done.exception()  # read, so one nobody waited for isn't logged as lost

    def _result(self, body: Mapping[str, object]) -> LookupResult:
        return LookupResult(
            json.dumps({"source": SOURCE, **body}, ensure_ascii=False, separators=(",", ":"))
        )


async def _by(
    deadline: float, fetches: list[Coroutine[Any, Any, list[dict[str, Any]]]]
) -> list[list[dict[str, Any]] | BaseException]:
    """Each fetch's answer or error, in order, waiting only until ``deadline`` (the
    loop's time): one still going then is a ``TimeoutError`` (#667)."""
    tasks = [asyncio.ensure_future(f) for f in fetches]
    if not tasks:
        return []
    # Each fetch starts before the deadline counts (#669): with no time left, one
    # cancelled before its first step would never fetch, or keep it for the next ask.
    await asyncio.sleep(0)
    left = max(0.0, deadline - asyncio.get_running_loop().time())
    try:
        _, late = await asyncio.wait(tasks, timeout=left)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    for task in late:
        task.cancel()  # only its waiting: a shared fetch carries on (``_get``)
    out: list[list[dict[str, Any]] | BaseException] = []
    for task in tasks:
        if task in late:
            out.append(TimeoutError())
        elif (error := task.exception()) is not None:
            out.append(error)
        else:
            out.append(task.result())
    return out


def _key(name: str) -> str:
    """A name without the game's ore marks, for matching: "Raw Ice" and "Quantainium
    (Raw)" are "ice" and "quantainium"."""
    for mark in ("(Raw)", "(Ore)", "(R)"):
        name = name.replace(mark, "")
    words = [w for w in name.lower().split() if w not in {"raw", "ore"}]
    return " ".join(words)


def _plain(said: str) -> str:
    """ "raw quantainium ore" -> "Quantainium": the forms add the game's marks back."""
    words = [w for w in said.split() if w.lower() not in {"raw", "ore", "unrefined", "refined"}]
    return " ".join(w.capitalize() for w in words) or said.capitalize()


def _in(location: Mapping[str, Any], place: str) -> bool:
    """Whether the location is the place, is on it, or is in its system."""
    key = "".join(w for w in normalize(place) if w not in {"the", "system"})
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
