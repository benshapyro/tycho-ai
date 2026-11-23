"""Which missions give a blueprint, and which blueprints a mission gives (#596), from
the game files via the Star Citizen Wiki API: satisfies ``brain.lookups.Lookup``.

The API can't filter missions by the blueprint they give, so the missions that give
any (``/api/missions?filter[has_blueprints]=1``, 794 on 4.10.3, four pages of 200 in
about 0.9 s) are loaded once and indexed both ways. They change only with a patch, so
they're kept for hours.

A mission's blueprints come in pools: on completion each pool rolls its chance and, if
it hits, gives one of its blueprints (the community reads them as equally likely). The
pools are only on a mission's own record, fetched for the missions asked about and
left out if they're slow.
"""

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from tycho.brain.lookups import LookupResult, failure
from tycho.games.star_citizen import blueprint_names as naming
from tycho.games.star_citizen.owned import OwnedBlueprints, mark
from tycho.games.star_citizen.wiki import WikiError, WikiSource

log = logging.getLogger(__name__)

SOURCE = "Star Citizen Wiki API (api.star-citizen.wiki), from the game files"
SECONDS = 4.0
"""The missions load in about 0.9 s (four pages at once, 23 November 2026), then the
pools of the missions asked about in about 0.3 s; both are kept, so a later question
is instant."""
POOL_SECONDS = 1.5
"""The pools are left out after this, rather than the answer waiting."""
PAGE = 200
MAX_PAGES = 10
KEEP_SECONDS = 6 * 3600.0
"""Missions change only with a patch: what was loaded is kept for six hours."""
SHOWN_MISSIONS = 6
SHOWN_BLUEPRINTS = 12
ALSO = 3
MAX_POOL_RECORDS = 4
"""A title's variants whose records are read for its pools, at most."""
NO_STANDING = frozenset({"", "neutral"})

DESCRIPTION = """\
Which missions give a crafting blueprint, and which blueprints a mission or mission \
giver gives, from the current patch's game files: the mission's title, who gives it, \
the system, the reputation standing needed with that giver, and the pool it's in: \
on completion a pool rolls pool_chance_percent and gives one of its pool_size \
blueprints, so say "one of 10" rather than calling a blueprint certain. \
Pass blueprint (the item it makes, as the player said it: "Omnisky III", "P4-AR") \
for "where do I get the X blueprint", or mission (a mission title or a giver: \
"Vaughn", "Headhunters") for "what blueprints does X give"; leave the other empty. \
For a blueprint's ingredients use item_lookup. Missions don't say exactly where \
they're offered: don't guess a place beyond the system. you_own_it or you_own: the \
player already has those blueprints, so say so. In the answer, credit the \
Star Citizen Wiki once, in a few words. A result with not_found: say briefly what \
wasn't found."""


_Pool = tuple[float, list[str]]
"""A pool's chance of giving a blueprint on completion, and the blueprints it picks from."""


@dataclass
class _Mission:
    """Missions with one title from one giver: the game has several variants of most."""

    title: str
    giver: str
    systems: list[str] = field(default_factory=lambda: list[str]())
    standing: str = ""
    standing_rep: float = float("inf")
    blueprints: list[str] = field(default_factory=lambda: list[str]())
    chain: bool = False
    illegal: bool = False
    records: list[str] = field(default_factory=lambda: list[str]())
    """The API paths of its variants' own records, which hold the pools."""
    record_for: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    """The first variant's record that gives each blueprint: variants' pools differ."""
    pool_records: list[str] = field(default_factory=lambda: list[str]())
    """One record for each different set of blueprints its variants give, so a title's
    pools cover them all."""

    def said(self) -> dict[str, object]:
        out: dict[str, object] = {"title": self.title, "giver": self.giver}
        if self.systems:
            out["systems"] = self.systems
        if self.standing.lower() not in NO_STANDING:
            out["standing_needed"] = self.standing
        if self.chain:
            out["after_earlier_missions"] = True
        if self.illegal:
            out["illegal"] = True
        return out


@dataclass
class _Index:
    missions: list[_Mission]
    by_blueprint: dict[str, list[_Mission]]
    patch: str


class BlueprintLookup:
    seconds = SECONDS

    def __init__(
        self, source: WikiSource, clock: Any = time.monotonic, owned: OwnedBlueprints | None = None
    ) -> None:
        """``owned``: the player's blueprints (#598), to say which they already have."""
        self._source = source
        self._owned = owned
        self._clock = clock
        self._index: _Index | None = None
        self._loaded_at = 0.0
        self._loading: asyncio.Task[_Index] | None = None
        self._kept_pools: dict[str, list[_Pool]] = {}

    @property
    def name(self) -> str:
        return "blueprint_lookup"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "blueprint": {
                    "type": "string",
                    "description": (
                        "The item the blueprint makes, as the player said it; otherwise empty."
                    ),
                },
                "mission": {
                    "type": "string",
                    "description": (
                        "A mission title or mission giver, as the player said it; otherwise empty."
                    ),
                },
            },
            "required": ["blueprint", "mission"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        """Load the missions once, at start. Never raises: a failure is tried again
        when the look-up runs."""
        try:
            await self._missions()
        except WikiError as e:
            log.warning("Blueprint missions didn't load: %s", e)

    async def blueprint_names(self) -> list[str]:
        """Every blueprint a mission gives in this patch. Raises ``WikiError``."""
        return list((await self._missions()).by_blueprint)

    async def close(self) -> None:
        if self._loading is not None and not self._loading.done():
            self._loading.cancel()

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        blueprint = " ".join(str(arguments.get("blueprint") or "").split())
        mission = " ".join(str(arguments.get("mission") or "").split())
        if not blueprint and not mission:
            return self._result({"not_found": "nothing asked: ask which blueprint or mission"})
        try:
            index = await self._missions()
        except WikiError as e:
            log.warning("Blueprint look-up failed: %s", e)
            return failure(f"the Star Citizen Wiki API didn't answer ({e}); answer without it")
        if blueprint:
            body = await self._blueprint(index, blueprint)
        else:
            body = await self._mission(index, mission)
        if self._owned is not None:
            body = mark(body, await asyncio.to_thread(self._owned.listed))  # no log read
        return self._result({"game_version": index.patch, **body})

    async def _blueprint(self, index: _Index, said: str) -> dict[str, object]:
        ranked = naming.ranked(said, index.by_blueprint)
        if not ranked:
            return {
                "asked": said,
                "not_found": "no mission in this patch gives a blueprint by that name; "
                "it may be known from the start or come from an event (item_lookup says)",
                "closest": naming.closest(said, index.by_blueprint),
            }
        name = ranked[0]
        missions = sorted(index.by_blueprint[name], key=lambda m: m.standing_rep)
        shown = missions[:SHOWN_MISSIONS]
        pools = await self._pools_of([m.record_for.get(name, "") for m in shown])
        said_missions: list[dict[str, object]] = []
        for m, mission_pools in zip(shown, pools, strict=True):
            entry = m.said()
            if pool := next((p for p in mission_pools if name in p[1]), None):
                entry["pool_chance_percent"] = round(100 * pool[0])
                entry["pool_size"] = len(pool[1])
            said_missions.append(entry)
        out: dict[str, object] = {
            "blueprint": name,
            "given_by": len(missions),
            "missions": said_missions,
        }
        if len(ranked) > 1:
            out["also_matches"] = ranked[1 : 1 + ALSO]
        return out

    async def _title_pools(self, missions: Sequence[_Mission]) -> list[list[_Pool]]:
        """Each mission's pools across its variants (#600): one record for each set of
        blueprints they give, the same pool listed once. A mission any of whose records
        didn't load has none, so its full list is said instead."""
        paths = [p for m in missions for p in m.pool_records[:MAX_POOL_RECORDS]]
        loaded = dict(zip(paths, await self._pools_of(paths), strict=True))
        out: list[list[_Pool]] = []
        for m in missions:
            pools: list[_Pool] = []
            complete = bool(m.pool_records)
            for path in m.pool_records[:MAX_POOL_RECORDS]:
                complete = complete and bool(loaded.get(path))
                pools.extend(p for p in loaded.get(path, []) if p not in pools)
            given = {n for _, names in pools for n in names}
            complete = complete and set(m.blueprints) <= given
            out.append(pools if complete else [])
        return out

    async def _pools_of(self, paths: Sequence[str]) -> list[list[_Pool]]:
        """The pools on each mission record, all at once. A record that's slow or
        missing has none: the answer goes on without them."""
        try:
            return await asyncio.wait_for(
                asyncio.gather(*(self._pools(p) for p in paths)), POOL_SECONDS
            )
        except TimeoutError:
            log.info("Mission pools didn't load in time")
            return [self._kept_pools.get(p, []) for p in paths]

    async def _pools(self, path: str) -> list[_Pool]:
        if not path:
            return []
        if path in self._kept_pools:
            return self._kept_pools[path]
        try:
            data = await self._source.get(path)
        except WikiError as e:
            log.info("A mission's pools didn't load: %s", e)
            return []
        body = cast(dict[str, Any], data) if isinstance(data, dict) else {}
        found = body.get("data")
        record = cast(dict[str, Any], found) if isinstance(found, dict) else {}  # #602
        pools: list[_Pool] = []
        for pool in cast(list[Any], record.get("blueprints") or []):
            if not isinstance(pool, dict):
                continue
            p = cast(dict[str, Any], pool)
            chance = p.get("drop_chance")
            names = [
                str(cast(dict[str, Any], i).get("name") or "")
                for i in cast(list[Any], p.get("items") or [])
                if isinstance(i, dict)
            ]
            if isinstance(chance, int | float) and names:
                pools.append((float(chance), [n for n in names if n]))
        self._kept_pools[path] = pools
        return pools

    async def _missions(self) -> _Index:
        if self._index is not None and self._clock() - self._loaded_at < KEEP_SECONDS:
            return self._index
        if self._loading is None or self._loading.done():
            self._loading = asyncio.ensure_future(self._load())
        try:
            index = await asyncio.shield(self._loading)
        except WikiError:
            self._loading = None
            raise
        if index is not self._index:
            self._kept_pools.clear()  # pools follow the missions they came with (#600)
        self._index, self._loaded_at = index, self._clock()
        return index

    async def _load(self) -> _Index:
        first = await self._page(1)
        meta = first.get("meta")
        last = min(_last_page(meta), MAX_PAGES)
        rest = await asyncio.gather(*(self._page(n) for n in range(2, last + 1)))
        try:  # an odd shape anywhere is the Wiki's problem, never a crash (#602)
            records = [
                cast(dict[str, Any], r)
                for page in (first, *rest)
                for r in cast(list[Any], page.get("data") or [])
                if isinstance(r, dict)
            ]
            return _indexed(records)
        except (AttributeError, TypeError, ValueError) as e:
            raise WikiError(f"/api/missions: unexpected shape ({e})") from e

    async def _page(self, number: int) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "filter[has_blueprints]": 1,
            "page[size]": PAGE,
            "page[number]": number,
        }
        data = await self._source.get("/api/missions", params)
        if not isinstance(data, dict):
            raise WikiError("/api/missions: not an object")
        return cast(dict[str, Any], data)

    async def _mission(self, index: _Index, said: str) -> dict[str, object]:
        """A giver's blueprints, or one mission title's, whichever fits what was said."""
        givers: dict[str, list[_Mission]] = {}
        titles: dict[str, list[_Mission]] = {}
        for m in index.missions:
            givers.setdefault(m.giver, []).append(m)
            titles.setdefault(m.title, []).append(m)
        giver_ranked = naming.ranked(said, givers)
        title_ranked = naming.ranked(said, titles)
        giver_fit = naming.score(said, giver_ranked[0]) if giver_ranked else 0.0
        title_fit = naming.score(said, title_ranked[0]) if title_ranked else 0.0
        if not giver_ranked and not title_ranked:
            return {
                "asked": said,
                "not_found": (
                    "no mission or mission giver by that name gives blueprints in this patch"
                ),
                "closest": naming.closest(said, {**givers, **titles}),
            }
        if giver_ranked and giver_fit >= title_fit:
            giver = giver_ranked[0]
            missions = givers[giver]
            blueprints = _union(missions)
            return {
                "giver": giver,
                "missions_with_blueprints": len(missions),
                "blueprint_count": len(blueprints),
                **_listed(blueprints),
                "systems": _union_systems(missions),
                "lowest_standing_needed": _lowest_standing(missions),
            }
        title = title_ranked[0]
        missions = titles[title]
        out: dict[str, object] = {"mission": title}
        givers_said: list[dict[str, object]] = []
        shown = missions[:ALSO]
        for m, pools in zip(shown, await self._title_pools(shown), strict=True):
            entry = m.said()
            entry.pop("title", None)
            entry["blueprint_count"] = len(m.blueprints)
            if pools:
                entry["pools"] = [
                    {
                        "pool_chance_percent": round(100 * chance),
                        "pool_size": len(names),
                        **_listed(names),
                    }
                    for chance, names in pools
                ]
            else:
                entry.update(_listed(m.blueprints))
            givers_said.append(entry)
        out["from"] = givers_said
        if len(title_ranked) > 1:
            out["also_matches"] = title_ranked[1 : 1 + ALSO]
        return out

    def _result(self, body: Mapping[str, object]) -> LookupResult:
        return LookupResult(
            json.dumps({"source": SOURCE, **body}, ensure_ascii=False, separators=(",", ":"))
        )


def _last_page(meta: object) -> int:
    """The page count, sent as an int or a digit string (#602); 1 when it's odd."""
    pages = str(cast(dict[str, Any], meta).get("last_page") if isinstance(meta, dict) else 1)
    return int(pages) if pages.isdecimal() and int(pages) > 0 else 1


def _indexed(records: Sequence[Mapping[str, Any]]) -> _Index:
    """The missions grouped by title and giver, and the blueprints they give. A
    blueprint's missions take their facts only from the variants that give it: one
    variant can be illegal, or need standing, when another doesn't."""
    variants: dict[tuple[str, str], list[_Mission]] = {}
    patch = ""
    for r in records:
        title = str(r.get("title") or "").strip()
        giver = str(r.get("mission_giver") or "").strip() or _faction(r)
        if not title:
            continue
        patch = patch or str(r.get("game_version") or "")
        variants.setdefault((title, giver), []).append(_variant(r, title, giver))
    missions: list[_Mission] = []
    by_blueprint: dict[str, list[_Mission]] = {}
    for group in variants.values():
        missions.append(_merged(group))
        names = dict.fromkeys(b for v in group for b in v.blueprints)
        for name in names:
            giving = [v for v in group if name in v.blueprints]
            by_blueprint.setdefault(name, []).append(_merged(giving))
    return _Index(missions, by_blueprint, patch)


def _variant(r: Mapping[str, Any], title: str, giver: str) -> _Mission:
    """One mission record."""
    m = _Mission(title=title, giver=giver)
    for system in cast(list[Any], r.get("star_systems") or []):
        name = _system(system)
        if name and name not in m.systems:
            m.systems.append(name)
    standing = r.get("min_standing")
    standing = cast(dict[str, Any], standing) if isinstance(standing, dict) else {}
    rep = standing.get("min_reputation")
    m.standing_rep = float(rep) if isinstance(rep, int | float) else 0.0
    m.standing = str(standing.get("name") or r.get("min_standing_name") or "")
    if path := _path(str(r.get("link") or "")):
        m.records.append(path)
    m.chain = bool(r.get("has_prerequisites"))
    m.illegal = bool(r.get("illegal"))
    for bp in cast(list[Any], r.get("blueprints") or []):
        if not isinstance(bp, dict):
            continue
        name = str(cast(dict[str, Any], bp).get("name") or "").strip()
        if name and name not in m.blueprints:
            m.blueprints.append(name)
    return m


def _merged(variants: Sequence[_Mission]) -> _Mission:
    """Variants of one mission as one: every system and blueprint, the lowest standing,
    and a chain or illegal if any variant is."""
    m = _Mission(title=variants[0].title, giver=variants[0].giver)
    seen_sets: set[tuple[str, ...]] = set()
    for v in variants:
        m.systems.extend(s for s in v.systems if s not in m.systems)
        if v.standing_rep < m.standing_rep:
            m.standing_rep, m.standing = v.standing_rep, v.standing
        m.chain = m.chain or v.chain
        m.illegal = m.illegal or v.illegal
        m.records.extend(v.records)
        path = v.records[0] if v.records else ""
        for name in v.blueprints:
            if name not in m.blueprints:
                m.blueprints.append(name)
            if path:
                m.record_for.setdefault(name, path)
        key = tuple(sorted(v.blueprints))
        if path and key not in seen_sets:
            seen_sets.add(key)
            m.pool_records.append(path)
    return m


def _listed(names: Sequence[str]) -> dict[str, object]:
    """The first few names, and how many more there are, so a count isn't read off
    the list."""
    out: dict[str, object] = {"blueprints": list(names[:SHOWN_BLUEPRINTS])}
    if len(names) > SHOWN_BLUEPRINTS:
        out["more_not_listed"] = len(names) - SHOWN_BLUEPRINTS
    return out


def _union(missions: Sequence[_Mission]) -> list[str]:
    return list(dict.fromkeys(b for m in missions for b in m.blueprints))


def _union_systems(missions: Sequence[_Mission]) -> list[str]:
    return list(dict.fromkeys(s for m in missions for s in m.systems))


def _lowest_standing(missions: Sequence[_Mission]) -> str:
    lowest = min(missions, key=lambda m: m.standing_rep)
    return lowest.standing if lowest.standing.lower() not in NO_STANDING else "none"


def _faction(record: Mapping[str, Any]) -> str:
    faction = record.get("faction")
    if isinstance(faction, dict):
        return str(cast(dict[str, Any], faction).get("name") or "")
    return ""


def _system(entry: object) -> str:
    name = (
        str(cast(dict[str, Any], entry).get("name") or "")
        if isinstance(entry, dict)
        else str(entry)
    )
    return name.replace(" System", "").strip()


def _path(link: str) -> str:
    """ "https://api.star-citizen.wiki/api/blueprints/x" -> "/api/blueprints/x"."""
    at = link.find("/api/")
    return link[at:] if at >= 0 else ""
