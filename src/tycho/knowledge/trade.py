"""Trade (ADR 15): commodity prices and trade routes from UEX, live.

UEX's prices are reported by players and update through the day, so nothing is
kept on disk: the commodity and terminal lists load at startup, and each price
or route list is fetched when asked and reused for ``FRESH_SECONDS``.

Names are matched in code, as ship names are: "quantanium" is Quantainium,
"Area18" and "Area 18" are the same place, and "raw" or "ore" picks the
unrefined commodity.
"""

import asyncio
import datetime
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from tycho.knowledge.fresh import Fresh
from tycho.knowledge.wiki import WikiError, WikiSource
from tycho.text import name_score, normalize, similarity

log = logging.getLogger(__name__)

FRESH_SECONDS = 600.0
"""How long a fetched price or route list is reused (UEX itself updates hourly)."""
TOP = 5
"""How many places or routes an answer lists."""
MAX_ORIGINS = 4
"""Terminals asked about at once when a route starts at a station, city or outpost."""
RAW_WORDS = frozenset({"raw", "ore", "unrefined"})
REFINED_WORDS = frozenset({"refined"})
"""UEX names a refined commodity plainly ("Gold"), and its ore "Gold (Ore)" or "(Raw)"."""
RAW_MARKS = ("(raw)", "(ore)")
GUESS_AMONG = 4
"""A name that fits this many or fewer is answered with the likeliest, and the others
named (#147); more than this, and the player is asked to name one."""
FILLER = frozenset({"the", "a", "an", "at", "in", "on", "near", "of", "some", "price", "prices"})
COMMODITY_TERMINALS = frozenset({"commodity", "commodity_raw"})
PLACE_FIELDS = (
    "city_name",
    "space_station_name",
    "outpost_name",
    "poi_name",
    "moon_name",
    "planet_name",
    "orbit_name",
    "star_system_name",
    "terminal",
)
"""Where a place name can match, in order: a place before a single terminal in it
("Area 18" is the city, with all its terminals), and a planet before its orbit
("Hurston" is the planet and its moons, not just the space around it)."""
PLACE_ALIASES = {"nb": "new babbage", "a18": "area 18", "grim hex": "grimhex"}


class TradeError(Exception):
    """A name couldn't be matched; the message says what to try."""


@dataclass(frozen=True, slots=True)
class Commodity:
    id: int
    name: str
    code: str
    raw: bool


@dataclass(frozen=True, slots=True)
class Place:
    """Where a place name matched: one field (``planet_name``…) and its value."""

    field: str
    value: str
    id_field: str
    """The id that names it in UEX's data, e.g. "id_planet"."""
    id: int


class Trade:
    """The commodity and terminal lists, and fetching prices and routes with a short reuse."""

    def __init__(self, source: WikiSource, clock: Callable[[], float] = time.monotonic) -> None:
        self._source = source
        self.commodities: list[Commodity] = []
        self.terminals: dict[int, Mapping[str, Any]] = {}
        self._fetched: Fresh[list[dict[str, Any]]] = Fresh(FRESH_SECONDS, clock)
        self._loading = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.commodities and self.terminals)

    async def load(self) -> None:
        """Fetch the commodity and terminal lists (two requests), once however many
        look-ups ask. Raises ``WikiError``."""
        async with self._loading:
            if self.ready:
                return
            commodities, terminals = await asyncio.gather(
                self._list("/commodities"), self._list("/terminals")
            )
            self.commodities = [
                Commodity(
                    int(c["id"]),
                    str(c["name"]),
                    str(c.get("code") or ""),
                    any(mark in str(c["name"]).lower() for mark in RAW_MARKS),
                )
                for c in commodities
                if c.get("is_available_live") and c.get("name")
            ]
            self.terminals = {int(t["id"]): t for t in terminals if t.get("id")}
            log.debug(
                "UEX: %d commodities, %d terminals", len(self.commodities), len(self.terminals)
            )

    def commodity(self, asked: str) -> Commodity:
        """The commodity the player meant. Raises ``TradeError`` with the closest names."""
        return self.commodity_match(asked)[0]

    def commodity_match(self, asked: str) -> tuple[Commodity, tuple[str, ...]]:
        """The commodity the player most likely meant, and any others that fit as well
        (#147: a best guess, not a question back, unless too many fit). Raises
        ``TradeError`` when none fits, or more than ``GUESS_AMONG`` do."""
        words = [w for w in normalize(asked) if w not in FILLER]
        raw = any(w in RAW_WORDS for w in words)
        words = [w for w in words if w not in RAW_WORDS and w not in REFINED_WORDS]
        if not words:
            raise TradeError("name a commodity")
        scored = sorted(
            ((_score(words, normalize(_plain(c.name)), c.code), c) for c in self.commodities),
            key=lambda pair: (-pair[0], pair[1].raw != raw, len(pair[1].name)),
        )
        best = scored[0][0] if scored else 0.0
        if best < 0.6:
            closest = ", ".join(c.name for _, c in scored[:3])
            raise TradeError(f"no commodity called {asked!r}; closest: {closest}")
        tied = [c for score, c in scored if score >= best - 0.03]
        names = list(dict.fromkeys(_plain(c.name) for c in tied))  # Gold, Gold (Ore): one
        if len(names) > GUESS_AMONG:
            raise TradeError(f"{asked!r} could be {', '.join(names[:4])}, and more: name one")
        chosen = [c for c in tied if _plain(c.name) == names[0]]
        return next((c for c in chosen if c.raw == raw), chosen[0]), tuple(names[1:])

    def place(self, asked: str) -> Place:
        """The most specific place the name matches. Raises ``TradeError``."""
        return self.place_match(asked)[0]

    def place_match(self, asked: str) -> tuple[Place, tuple[str, ...]]:
        """The most specific place the name matches, and any others it matches as well
        (a best guess among a few, #147). Raises ``TradeError`` when none matches, or
        more than ``GUESS_AMONG`` do ("Lagrange": name one)."""
        key = _squash(PLACE_ALIASES.get(" ".join(normalize(asked)), asked))
        key = _squash(" ".join(w for w in normalize(key) if w not in FILLER)) or key
        if not key:
            raise TradeError("name a place")
        # The whole name first, everywhere ("Pyro" is the system, not "Pyro Gateway"),
        # then part of a name or a misspelling.
        for exact in (True, False):
            for field in PLACE_FIELDS:
                values: dict[str, tuple[str, int]] = {}
                for terminal in self.terminals.values():
                    value = _terminal_name(terminal) if field == "terminal" else terminal.get(field)
                    if value and _place_matches(key, str(value), exact=exact):
                        id_field = "id" if field == "terminal" else _id_field(field)
                        values.setdefault(str(value), (id_field, int(terminal.get(id_field) or 0)))
                if len(values) > GUESS_AMONG:
                    choices = ", ".join(sorted(values)[:4])
                    raise TradeError(f"{asked!r} could be {choices}, and more: name one")
                if values:
                    # The shortest name is the likeliest ("Lorville" before "Lorville Gates").
                    ranked = sorted(values, key=lambda v: (len(v), v))
                    id_field, ident = values[ranked[0]]
                    return Place(field, ranked[0], id_field, ident), tuple(ranked[1:])
        raise TradeError(f"no place called {asked!r} in UEX's list; name a city, station or planet")

    def in_place(self, row: Mapping[str, Any], place: Place, prefix: str = "") -> bool:
        """Whether a price or route row is at ``place``."""
        if place.field == "terminal":
            return int(row.get(f"id_terminal{prefix}") or 0) == place.id
        terminal = self.terminals.get(int(row.get(f"id_terminal{prefix}") or 0), {})
        return str(terminal.get(place.field) or "") == place.value

    def terminals_at(self, place: Place) -> list[int]:
        """The commodity terminals at a place, for routes UEX can't start from a station."""
        return sorted(
            ident
            for ident, t in self.terminals.items()
            if t.get("type") in COMMODITY_TERMINALS
            and (
                ident == place.id
                if place.field == "terminal"
                else str(t.get(place.field) or "") == place.value
            )
        )

    def where(self, terminal_id: int) -> dict[str, str]:
        """A terminal as it's said: its short name, and the planet and system it's in."""
        t = self.terminals.get(terminal_id, {})
        around = ", ".join(
            str(v)
            for v in (t.get("moon_name") or t.get("planet_name"), t.get("star_system_name"))
            if v
        )
        return {"where": _terminal_name(t) or f"terminal {terminal_id}", "around": around}

    async def prices(self, commodity: Commodity) -> list[dict[str, Any]]:
        # Raw ore is priced separately, at refineries' ore sales terminals.
        path = "/commodities_raw_prices" if commodity.raw else "/commodities_prices"
        return await self._fresh(path, {"id_commodity": commodity.id})

    async def routes(self, params: Mapping[str, int]) -> list[dict[str, Any]]:
        return await self._fresh("/commodities_routes", params)

    async def _fresh(self, path: str, params: Mapping[str, int]) -> list[dict[str, Any]]:
        key = f"{path}?{sorted(params.items())}"
        if (hit := self._fetched.get(key)) is not None:
            return hit
        rows = await self._list(path, params)
        self._fetched.keep(key, rows)
        return rows

    async def _list(
        self, path: str, params: Mapping[str, int] | None = None
    ) -> list[dict[str, Any]]:
        data = await self._source.get(path, params)
        try:
            if data.get("status") != "ok":
                raise WikiError(f"UEX {path}: {data.get('message') or data.get('status')}")
            return cast(list[dict[str, Any]], data["data"] or [])
        except (AttributeError, KeyError, TypeError) as e:
            raise WikiError(f"UEX {path}: unexpected answer") from e


def hours_ago(timestamp: Any, now: float | None = None) -> float | None:
    if not isinstance(timestamp, int | float) or timestamp <= 0:
        return None
    now = datetime.datetime.now(datetime.UTC).timestamp() if now is None else now
    return round(max(0.0, now - timestamp) / 3600, 1)


def top(
    rows: Iterable[Mapping[str, Any]], key: str, *, cheapest: bool = False
) -> list[Mapping[str, Any]]:
    """The best rows by ``key``, leaving out zeros (not traded there)."""
    priced = [r for r in rows if isinstance(r.get(key), int | float) and r[key] > 0]
    return sorted(priced, key=lambda r: r[key] if cheapest else -r[key])[:TOP]


def _plain(name: str) -> str:
    """A commodity's name without its ore mark: "Gold (Ore)" is "Gold"."""
    for mark in ("(Raw)", "(Ore)"):
        name = name.replace(mark, "")
    return name.strip()


def _score(said: list[str], name: list[str], code: str) -> float:
    if len(said) == 1 and said[0] == code.lower():
        return 1.0
    return name_score(said, name)


def _squash(text: str) -> str:
    return "".join(normalize(text))


def _place_matches(key: str, value: str, *, exact: bool) -> bool:
    name = _squash(value)
    if exact:
        return key == name
    return (len(key) >= 4 and key in name) or (len(key) >= 5 and similarity(key, name) >= 0.85)


def _terminal_name(terminal: Mapping[str, Any]) -> str:
    return str(
        terminal.get("displayname") or terminal.get("nickname") or terminal.get("name") or ""
    )


def _id_field(field: str) -> str:
    return "id_" + re.sub(r"_name$", "", field)
