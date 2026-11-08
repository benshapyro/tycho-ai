"""Items (ADR 15): FPS weapons and armor, ship weapons and components, and how to
craft them, from the Star Citizen Wiki API, live.

A local copy would mean about 60 MB a patch (7,084 base items at about 8 KB, and
the API can't send just names), so each question asks the API instead (Ben,
2026-11-02). The API matches a name as a substring, exactly as written, so a name
is tried in a few spellings at once ("fs9" as "fs-9", "arrow head" as "arrowhead",
its most distinctive word alone), and the results are ranked here.

The API's unified search is asked at the same time (#153). It finds ships too, so
a ship asked of the item look-up ("Cutlass Black", whose plushie is an item) says
it's a ship, and it can rescue a miss: an item its name fits, or the closest names
across ships and items.
"""

import asyncio
import itertools
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from tycho.knowledge.fresh import Fresh
from tycho.knowledge.wiki import ITEM_KINDS, VEHICLES, Hit, WikiError, WikiSource, search
from tycho.text import name_score, normalize

log = logging.getLogger(__name__)

MATCH = 0.6
TIE = 0.03
CHOICES = 3
FRESH_SECONDS = 600.0
PER_QUERY = 20
QUERY_SECONDS = 2.5
"""Each query's limit: a spelling, the search or a recipe that takes longer is left out."""
FIND_SECONDS = 3.0
"""A name's whole finding, fetching an item the search rescued included, so the
recipe still has its QUERY_SECONDS inside item_lookup's 6 s."""
FILLER = frozenset({"the", "a", "an", "my", "this", "that", "item", "uh", "um", "erm"})
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


@dataclass(frozen=True, slots=True)
class IsAShip:
    """The name is a ship's, at least as much as any item's: ship_lookup knows it."""

    asked: str
    ship: str


class Items:
    """Finds items by what the player called them, with a short reuse of each answer."""

    def __init__(self, source: WikiSource, clock: Callable[[], float] = time.monotonic) -> None:
        self._source = source
        self._clock = clock
        self._fetched: Fresh[Mapping[str, Any]] = Fresh(FRESH_SECONDS, clock)
        self._searched: Fresh[list[Hit]] = Fresh(FRESH_SECONDS, clock)

    async def find(self, asked: str) -> Found | Unclear | Missing | IsAShip:
        """Raises ``WikiError`` if the API can't be reached."""
        started = self._clock()
        said = [spoken(asked), *swapped(asked)]
        words = [w for w in normalize(said[0]) if w not in FILLER]
        if not words:
            return Missing(asked, ())
        queries = list(dict.fromkeys(q for s in said for q in spellings(s)))
        hits, pages = await asyncio.gather(
            self._in_time(self._hits(search_text(asked))),
            asyncio.gather(*(self._in_time(self._search(q)) for q in queries)),
        )
        if all(page is None for page in pages) and not hits:
            raise WikiError("the Wiki API took too long or didn't answer")
        records: dict[str, Mapping[str, Any]] = {}
        for page in pages:
            for record in page or []:
                if record.get("type") not in NOT_ITEMS:
                    records.setdefault(str(record.get("uuid") or record.get("name")), record)
        readings = [
            r for s in said for r in _readings([w for w in normalize(s) if w not in FILLER])
        ]
        scored = sorted(
            ((_score(readings, r), r) for r in records.values()),
            key=lambda pair: (-pair[0], len(str(pair[1].get("name") or ""))),
        )
        best = scored[0][0] if scored else 0.0
        ranked = _ranked(readings, hits or [])
        # A ship must fit the words as said, kind-of-item words and all: "Mantis
        # gatling" is the Mantis GT-220 Gatling, not the RSI Mantis (#220 review).
        as_said = [_squashed_codes(words)]
        ships = sorted(
            (
                (_score(as_said, {"name": hit.name}), hit)
                for _, hit in ranked
                if hit.kind == VEHICLES
            ),
            key=lambda pair: (-pair[0], len(pair[1].name)),
        )
        if ships and ships[0][0] >= MATCH and ships[0][0] >= best - TIE:
            # "Cutlass Black" is the ship, not the Cutlass Black Plushie.
            return IsAShip(asked, ships[0][1].name)
        if best < MATCH:
            closest = [str(r.get("name")) for _, r in scored[:CHOICES]]
            return await self._rescued(Missing(asked, tuple(closest)), readings, ranked, started)
        tied = [r for score, r in scored if score >= best - TIE]
        # A name exactly as heard wins over one exactly as a swapped version (#174):
        # "F S nine" is the FS-9, even with an SF-9 there too.
        heard = _readings(words)
        exact = (
            [r for r in tied if _words(r) in heard]
            or [r for r in tied if _words(r) in readings]
            # Only against a swapped version: a vague name ("Morozov") still asks which.
            or [r for r in tied if len(said) > 1 and any(set(h) <= set(_words(r)) for h in heard)]
        )
        if len(tied) == 1 or exact:
            return Found((exact or tied)[0])
        return Unclear(asked, tuple(str(r.get("name")) for r in tied[:CHOICES]))

    async def blueprint(self, item: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """How to craft ``item``, if it can be crafted."""
        if not item.get("is_craftable"):
            return None
        data = await self._in_time(
            self._get("/api/blueprints", {"filter[output.name]": str(item["name"])})
        )
        if data is None:
            return None
        rows = cast(list[Mapping[str, Any]], data.get("data") or [])
        return next((r for r in rows if r.get("output_name") == item["name"]), None)

    async def _rescued(
        self,
        missed: Missing,
        readings: list[list[str]],
        ranked: list[tuple[float, Hit]],
        started: float,
    ) -> Found | Unclear | Missing:
        """A miss, rescued by the search where it can be: an item whose name fits,
        else the closest names across items and ships."""
        found = [(score, hit) for score, hit in ranked if hit.kind in ITEM_KINDS]
        best = found[0][0] if found else 0.0
        if best >= MATCH:
            tied = list(dict.fromkeys(hit.name for score, hit in found if score >= best - TIE))
            if len(tied) > 1:
                return Unclear(missed.asked, tuple(tied[:CHOICES]))
            left = min(QUERY_SECONDS, FIND_SECONDS - (self._clock() - started))
            if left > 0 and (record := await self._in_time(self._record(tied[0]), left)):
                return Found(record)
        names = [*missed.closest, *(hit.name for _, hit in ranked)]
        closest = sorted(
            dict.fromkeys(names), key=lambda n: (-_score(readings, {"name": n}), len(n))
        )
        return Missing(missed.asked, tuple(closest[:CHOICES]))

    async def _in_time[T](self, call: Awaitable[T], seconds: float | None = None) -> T | None:
        """``call``'s answer, or None if the API takes longer than ``QUERY_SECONDS``.
        The API is quick once it has seen a query, but a new one can take 10 s or more."""
        seconds = QUERY_SECONDS if seconds is None else seconds
        try:
            return await asyncio.wait_for(call, seconds)
        except TimeoutError:
            log.debug("A Wiki API query took over %.1f s; going on without it", seconds)
            return None
        except WikiError as e:
            # One spelling's query failing (a rate limit, say) doesn't fail the others.
            log.debug("A Wiki API query failed (%s); going on without it", e)
            return None

    async def _search(self, query: str) -> list[Mapping[str, Any]]:
        data = await self._get(
            "/api/items",
            {"filter[name]": query, "filter[variants]": "false", "limit": PER_QUERY},
        )
        return cast(list[Mapping[str, Any]], data.get("data") or [])

    async def _hits(self, query: str) -> list[Hit]:
        if (hit := self._searched.get(query)) is not None:
            return hit
        hits = await search(self._source, query)
        self._searched.keep(query, hits)
        return hits

    async def _record(self, name: str) -> Mapping[str, Any] | None:
        """The item named exactly ``name``, variants too ("Gallant "Midnight" Rifle")."""
        data = await self._get("/api/items", {"filter[name]": name, "limit": PER_QUERY})
        rows = cast(list[Mapping[str, Any]], data.get("data") or [])
        return next(
            (r for r in rows if r.get("name") == name and r.get("type") not in NOT_ITEMS), None
        )

    async def _get(self, path: str, params: Mapping[str, str | int]) -> Any:
        key = f"{path}?{sorted(params.items())}"
        if (hit := self._fetched.get(key)) is not None:
            return hit
        data = await self._source.get(path, params)
        if not isinstance(data, Mapping):
            raise WikiError(f"{path}: unexpected answer")
        answer = cast(Mapping[str, Any], data)
        self._fetched.keep(key, answer)
        return answer


NUMBERS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}
"""Numbers as speech-to-text writes them, said as part of a model code ("S F nine")."""


def spoken(asked: str) -> str:
    """A name as it was said, made written: letters said one at a time are joined and
    numbers become digits ("S F nine" is "SF 9", #153). Other words are as said."""
    out: list[str] = []
    joined = False
    for word in asked.split():
        bare = word.strip(".,;:!?")
        if bare.lower() in FILLER and not (bare.lower() == "a" and _in_code(out)):
            # "a S 38" is the S-38, and "the, uh, S F nine" the FS-9; but in "p four a
            # r", after part of a code, "a" is a letter.
            joined = False
            continue
        if len(bare) == 1 and bare.isalpha() and out and joined:
            out[-1] += bare
            continue  # still in the code: a number may follow
        elif len(bare) == 1 and bare.isalpha():
            out.append(bare)
            joined = True
            continue
        elif (digit := NUMBERS.get(bare.lower(), bare)).isdigit() and joined:
            out[-1] += digit  # a code's number joins its letters: "SF9", "P4"
        else:
            out.append(NUMBERS.get(bare.lower(), word))
        joined = False
    return " ".join(out)


def _in_code(written: list[str]) -> bool:
    """Whether the last word written is part of a code: letters said one by one, or
    letters and a number ("p4")."""
    last = written[-1] if written else ""
    return bool(last) and ((len(last) <= 3 and last.isalpha()) or any(c.isdigit() for c in last))


def swapped(asked: str) -> list[str]:
    """For a code said letter by letter, the same with its letters in another order:
    speech-to-text often swaps letters said quickly ("S F nine" for the FS-9). Two or
    three letters only, so at most five more tries."""
    letters = [w.strip(".,;:!?") for w in asked.split()]
    while letters and letters[0].lower() in FILLER:  # "a S F nine": the article goes
        letters = letters[1:]
    run: list[str] = []
    for word in letters:
        if len(word) == 1 and word.isalpha():
            run.append(word)
        elif run:
            break
    if not 2 <= len(run) <= 3:
        return []
    said = spoken(asked)
    code = "".join(run)
    return [
        said.replace(code, "".join(order), 1)
        for order in dict.fromkeys(itertools.permutations(run))
        if "".join(order) != code
    ]


def spellings(asked: str) -> list[str]:
    """The ways to ask the API for a name, most faithful first."""
    words = [w for w in asked.strip().split() if w.strip(".,;:!?").lower() not in FILLER]
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


def search_text(asked: str) -> str:
    """What to ask the unified search for a name: as said, made written, without
    filler or the words for the kind of item, and with model codes as the game
    writes them ("S F nine" is "sf-9", "Atlas quantum drive" is "atlas"). The search
    matches a substring, so one faithful phrase beats several loose ones."""
    words = [w for w in normalize(spoken(asked)) if w not in FILLER]
    words = [w for w in words if w not in GENERIC] or words
    return _CODE.sub(r"\1-\2", " ".join(words))


def _ranked(readings: list[list[str]], hits: list[Hit]) -> list[tuple[float, Hit]]:
    """The search's ships and items, best fit first; liveries and cargo left out."""
    kept = [
        hit
        for hit in hits
        if hit.kind == VEHICLES or (hit.kind in ITEM_KINDS and hit.label not in NOT_ITEMS)
    ]
    return sorted(
        ((_score(readings, {"name": hit.name}), hit) for hit in kept),
        # A ship wins a tie: "Drake Cutlass Black" over the Cutlass Black Plushie.
        key=lambda pair: (-pair[0], pair[1].kind != VEHICLES, len(pair[1].name)),
    )


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
