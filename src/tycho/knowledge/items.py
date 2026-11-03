"""Items (ADR 15): FPS weapons and armor, ship weapons and components, and how to
craft them, from the Star Citizen Wiki API, live.

A local copy would mean about 60 MB a patch (7,084 base items at about 8 KB, and
the API can't send just names), so each question asks the API instead (Ben,
2026-11-02). The API matches a name as a substring, exactly as written, so a name
is tried in a few spellings at once ("fs9" as "fs-9", "arrow head" as "arrowhead",
its most distinctive word alone), and the results are ranked here.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from tycho.knowledge.wiki import WikiError, WikiSource
from tycho.text import name_score, normalize

log = logging.getLogger(__name__)

MATCH = 0.6
TIE = 0.03
CHOICES = 3
FRESH_SECONDS = 600.0
PER_QUERY = 20
QUERY_SECONDS = 2.5
"""Each query's limit: a spelling or a recipe that takes longer is left out."""
FILLER = frozenset({"the", "a", "an", "my", "this", "that", "item"})
GENERIC = frozenset(
    {
        "rifle",
        "pistol",
        "shotgun",
        "sniper",
        "lmg",
        "smg",
        "cannon",
        "repeater",
        "gatling",
        "scattergun",
        "shield",
        "helmet",
        "armor",
        "armour",
        "arms",
        "legs",
        "core",
        "backpack",
        "drive",
        "quantum",
        "cooler",
        "power",
        "plant",
        "weapon",
        "gun",
        "magazine",
        "battery",
    }
)
"""Words that say what kind of item it is ("Atlas quantum drive", "Glacier cooler"):
too common to search on alone, and not held against a name that lacks them."""
NOT_ITEMS = frozenset({"Paints", "Cargo"})
"""Liveries and commodity boxes share names with items ("Arrow Headhunters Livery")."""
_CODE = re.compile(r"\b([a-z]{1,4})[\s-]?(\d+)\b")


@dataclass(frozen=True, slots=True)
class Found:
    record: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Unclear:
    asked: str
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Missing:
    asked: str
    closest: tuple[str, ...]


class Items:
    """Finds items by what the player called them, with a short reuse of each answer."""

    def __init__(self, source: WikiSource, clock: Callable[[], float] = time.monotonic) -> None:
        self._source = source
        self._clock = clock
        self._fetched: dict[str, tuple[float, Any]] = {}

    async def find(self, asked: str) -> Found | Unclear | Missing:
        """Raises ``WikiError`` if the API can't be reached."""
        words = [w for w in normalize(asked) if w not in FILLER]
        if not words:
            return Missing(asked, ())
        pages = await asyncio.gather(*(self._in_time(self._search(q)) for q in spellings(asked)))
        if all(page is None for page in pages):
            raise WikiError("the Wiki API took too long")
        records: dict[str, Mapping[str, Any]] = {}
        for page in pages:
            for record in page or []:
                if record.get("type") not in NOT_ITEMS:
                    records.setdefault(str(record.get("uuid") or record.get("name")), record)
        readings = _readings(words)
        scored = sorted(
            ((_score(readings, r), r) for r in records.values()),
            key=lambda pair: (-pair[0], len(str(pair[1].get("name") or ""))),
        )
        best = scored[0][0] if scored else 0.0
        if best < MATCH:
            return Missing(asked, tuple(str(r.get("name")) for _, r in scored[:CHOICES]))
        tied = [r for score, r in scored if score >= best - TIE]
        exact = [r for r in tied if _words(r) in readings]
        if len(tied) == 1 or exact:
            return Found((exact or tied)[0])
        return Unclear(asked, tuple(str(r.get("name")) for r in tied[:CHOICES]))

    async def blueprint(self, item: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """How to craft ``item``, if it can be crafted."""
        if not item.get("is_craftable"):
            return None
        data = await self._in_time(
            self._get("/api/v2/blueprints", {"filter[output.name]": str(item["name"])})
        )
        if data is None:
            return None
        rows = cast(list[Mapping[str, Any]], data.get("data") or [])
        return next((r for r in rows if r.get("output_name") == item["name"]), None)

    async def _in_time[T](self, call: Awaitable[T]) -> T | None:
        """``call``'s answer, or None if the API takes longer than ``QUERY_SECONDS``.
        The API is quick once it has seen a query, but a new one can take 10 s or more."""
        try:
            return await asyncio.wait_for(call, QUERY_SECONDS)
        except TimeoutError:
            log.debug("A Wiki API query took over %.1f s; going on without it", QUERY_SECONDS)
            return None

    async def _search(self, query: str) -> list[Mapping[str, Any]]:
        data = await self._get(
            "/api/v2/items",
            {"filter[name]": query, "filter[variants]": "false", "limit": PER_QUERY},
        )
        return cast(list[Mapping[str, Any]], data.get("data") or [])

    async def _get(self, path: str, params: Mapping[str, str | int]) -> Any:
        key = f"{path}?{sorted(params.items())}"
        if (hit := self._fetched.get(key)) and self._clock() - hit[0] < FRESH_SECONDS:
            return hit[1]
        data = await self._source.get(path, params)
        if not isinstance(data, Mapping):
            raise WikiError(f"{path}: unexpected answer")
        answer = cast(Mapping[str, Any], data)
        self._fetched[key] = (self._clock(), answer)
        return answer


def spellings(asked: str) -> list[str]:
    """The ways to ask the API for a name, most faithful first."""
    words = [w for w in asked.strip().split() if w.lower() not in FILLER]
    said = " ".join(words)
    lower = said.lower()
    tries = [said, _CODE.sub(r"\1-\2", lower)]
    plain = normalize(said)
    if len(plain) == 2 and not any(c.isdigit() for c in "".join(plain)):
        tries.append("".join(plain))  # "arrow head" is the Arrowhead
    if len(plain) >= 2 and all(len(w) <= 3 for w in plain):
        tries.append("-".join(plain))  # "p4 ar" is the P4-AR
    distinctive = [w for w in plain if w not in GENERIC and len(w) >= 4]
    if distinctive:
        tries.append(max(distinctive, key=len))
    seen: list[str] = []
    for query in tries:
        if query and query.lower() not in (s.lower() for s in seen):
            seen.append(query)
    return seen


def _readings(words: list[str]) -> list[list[str]]:
    """What the player may have meant, word by word: as said, without the words for
    the kind of item, and two words run together ("arrow head")."""
    said = _squashed_codes(words)
    readings = [said]
    if (core := [w for w in said if w not in GENERIC]) and core != said:
        readings.append(core)
    if len(said) == 2 and all(w.isalpha() for w in said):
        readings.append(["".join(said)])
    return readings


def _words(record: Mapping[str, Any]) -> list[str]:
    return _squashed_codes(normalize(str(record.get("name") or "")))


def _score(readings: list[list[str]], record: Mapping[str, Any]) -> float:
    name = _words(record)
    first, *others = readings
    # A looser reading counts a touch less than the words as said.
    return max([name_score(first, name), *(name_score(r, name) - 0.02 for r in others)])


def _squashed_codes(words: list[str]) -> list[str]:
    """Words with model codes joined ("fs", "9" -> "fs9"), so "FS-9" and "fs9" are one word."""
    out: list[str] = []
    for word in words:
        if out and word.isdigit() and out[-1].isalpha() and len(out[-1]) <= 4:
            out[-1] += word
        else:
            out.append(word)
    return out


# --- What Claude reads -------------------------------------------------------------------------


def facts(item: Mapping[str, Any], blueprint: Mapping[str, Any] | None = None) -> dict[str, object]:
    """The few facts players ask about, for each kind of item."""
    out: dict[str, object] = {
        "name": item.get("name"),
        "maker": _dig(item, "manufacturer", "name"),
        "kind": item.get("classification_label") or item.get("type_label"),
        "size": item.get("size"),
        "grade": item.get("grade"),
        "class": item.get("class"),
        "rarity": item.get("rarity"),
    }
    if weapon := item.get("personal_weapon"):
        out |= _weapon(weapon, personal=True)
    if weapon := item.get("vehicle_weapon"):
        out |= _weapon(weapon, personal=False)
    if shield := item.get("shield"):
        out |= {"shield_hp": _dig(shield, "max_health"), "regen_per_s": _dig(shield, "regen_rate")}
    if drive := item.get("quantum_drive"):
        out |= {
            "speed": _dig(drive, "standard_jump", "drive_speed_formatted"),
            "spool_s": _dig(drive, "standard_jump", "spool_up_time"),
            "fuel_scu_per_gm": _dig(drive, "fuel_consumption_scu_per_gm"),
            "time_for_10_gm": _dig(drive, "travel_time_10gm", "formatted"),
        }
    if plant := item.get("power_plant"):
        out["power_segments"] = _dig(plant, "power_segment_generation")
    if cooler := item.get("cooler"):
        out["coolant_segments"] = _dig(cooler, "coolant_segment_generation")
    if armor := item.get("suit_armor"):
        out |= _armor(armor)
    out["buy_in_game"] = _shops(_dig(item, "uex_prices", "purchase"))
    out["craftable"] = bool(item.get("is_craftable"))
    if blueprint:
        out["crafting"] = _crafting(blueprint)
    return {k: v for k, v in out.items() if v not in (None, "", [], {}, "UNDEFINED")}


def _weapon(weapon: Any, *, personal: bool) -> dict[str, object]:
    mode = _first(_dig(weapon, "modes"))
    out: dict[str, object] = {
        "weapon_type": _dig(weapon, "type"),
        "damage_class": _dig(weapon, "class") if personal else None,
        "damage_per_shot": _dig(weapon, "damage_per_shot"),
        "rounds_per_minute": _dig(weapon, "rpm"),
        "dps": _dig(mode, "damage_per_second"),
        "range_m": _dig(weapon, "effective_range" if personal else "range"),
    }
    rounds = _dig(weapon, "magazine_size" if personal else "capacity")
    if isinstance(rounds, int | float) and rounds > 0:  # energy guns have none
        out["magazine" if personal else "ammo"] = rounds
    return out


def _armor(armor: Any) -> dict[str, object]:
    reduction: dict[str, int] = {}
    resistance = _dig(armor, "damage_resistance")
    if isinstance(resistance, Mapping):
        for kind, value in cast(Mapping[str, Any], resistance).items():
            multiplier = _dig(value, "multiplier") if isinstance(value, Mapping) else value
            if isinstance(multiplier, int | float) and kind != "impact":
                reduction[kind] = round((1 - multiplier) * 100)
    return {"armor_type": _dig(armor, "armor_type"), "damage_reduction_pct": reduction}


def _crafting(blueprint: Mapping[str, Any]) -> dict[str, object]:
    needs: list[str] = []
    for part in cast(list[Any], blueprint.get("ingredients") or []):
        name, scu, count = _dig(part, "name"), _dig(part, "quantity_scu"), _dig(part, "quantity")
        amount = f"{scu:g} SCU" if isinstance(scu, int | float) else f"x{count}" if count else ""
        needs.append(f"{name} {amount}".strip())
    out: dict[str, object] = {
        "ingredients": needs,
        "craft_time": blueprint.get("craft_time_label"),
        "known_from_start": bool(blueprint.get("is_available_by_default")),
        "unlocked_by_missions": blueprint.get("unlocking_missions_count"),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _shops(rows: Any) -> list[dict[str, object]]:
    if not isinstance(rows, list):
        return []
    shops: list[dict[str, object]] = [
        {"where": str(_dig(r, "terminal_name")), "auec": _dig(r, "price_buy")}
        for r in cast(list[Any], rows)
        if isinstance(_dig(r, "price_buy"), int | float) and _dig(r, "price_buy") > 0
    ]
    return sorted(shops, key=lambda s: cast(float, s["auec"]))[:3]


def _first(value: Any) -> Any:
    return cast(list[Any], value)[0] if isinstance(value, list) and value else None


def _dig(record: Any, *path: str) -> Any:
    value: Any = record
    for step in path:
        if not isinstance(value, Mapping):
            return None
        value = cast(Mapping[str, Any], value).get(step)
    return value
