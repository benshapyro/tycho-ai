"""Ships and ground vehicles: the compact facts Tycho speaks, and finding a ship by
what the player called it (ADR 15, #54).

The Wiki API's records are large (about 70 KB each); ``ship_from_wiki`` keeps the
two dozen facts players ask about. Names are free text: "Connie", "the Cat",
"Cutlass Black". ``ShipLibrary.find`` turns slang into the full names, then
matches word by word, forgiving small misspellings. When several ships fit
equally well ("Constellation"), it says which, so Claude can ask or choose.

Ships still in concept aren't in the game files; ``concepts_from_uex`` adds them
from UEX, marked as not in the game, so "the Kraken" finds something true.

A name that matches no ship may be an item's ("the Gallant"): ``rescued`` reads the
Wiki API's unified search for it (#153).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, cast

from tycho.knowledge.items import GENERIC, NOT_ITEMS, search_text
from tycho.knowledge.wiki import ITEM_KINDS, VEHICLES, Hit
from tycho.text import name_score, normalize, similarity

MATCH = 0.6
"""The lowest score that counts as a match; below it, the closest names are offered."""
TIE = 0.03
"""Scores this close to the best are equally good matches."""
REMEMBERED = 1024
"""How many names ``ShipLibrary.find`` remembers the answer for."""
CHOICES = 3
"""How many candidates to offer when a name is ambiguous or unknown."""

ALIASES: dict[str, tuple[str, ...]] = {
    "connie": ("constellation",),
    "conny": ("constellation",),
    "cat": ("caterpillar",),
    "cutty": ("cutlass",),
    "herc": ("hercules",),
    "msr": ("mercury", "star", "runner"),
    "mercury": ("mercury", "star", "runner"),
    "mark": ("mk",),
    "mk2": ("mk", "ii"),
    "mk4": ("mk", "iv"),
    "mk5": ("mk", "v"),
}
"""Players' names for ships, word by word. Add to it when a player's name misses."""
FILLER = frozenset({"the", "a", "an", "my", "ship", "ships"})
NUMERALS = {"1": "i", "one": "i", "2": "ii", "two": "ii", "4": "iv", "four": "iv", "5": "v"}
"""Mark numbers as the names write them: "Mk 2" is "Mk II"."""
CONCEPT_KEY = "uex:"
"""Keys of ships from UEX's concept list start with this."""
CONCEPT_STATUS = "concept: announced and sold, not in the game yet; details may change"
ROLE_FLAGS = {
    "boarding": "boarding",
    "bomber": "bomber",
    "cargo": "cargo",
    "carrier": "carrier",
    "construction": "construction",
    "datarunner": "data running",
    "docking": "docking",
    "emp": "EMP",
    "exploration": "exploration",
    "hangar": "hangar",
    "industrial": "industrial",
    "interdiction": "interdiction",
    "medical": "medical",
    "military": "military",
    "mining": "mining",
    "passenger": "passenger",
    "qed": "quantum interdiction",
    "racing": "racing",
    "refinery": "refining",
    "refuel": "refuelling",
    "repair": "repair",
    "research": "research",
    "salvage": "salvage",
    "scanning": "scanning",
    "science": "science",
    "stealth": "stealth",
    "tractor_beam": "tractor beam",
}
"""UEX's role flags ("is_mining"), as said."""


@dataclass(frozen=True, slots=True)
class Ship:
    key: str
    """The game's class name, e.g. "RSI_Constellation_Andromeda"."""
    name: str
    """Without the maker, e.g. "Constellation Mk IV Andromeda"."""
    maker: str
    facts: Mapping[str, object]
    """What Claude reads: compact, spoken-ready, JSON-safe."""
    names: tuple[str, ...] = ()
    """Every name to match on: the name, the full name with the maker, and so on."""
    _keys: tuple[tuple[str, ...], ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        keys = {tuple(normalize(n)) for n in (self.name, *self.names)}
        object.__setattr__(self, "_keys", tuple(k for k in keys if k))

    @property
    def keys(self) -> tuple[tuple[str, ...], ...]:
        return self._keys

    @property
    def is_concept(self) -> bool:
        return self.key.startswith(CONCEPT_KEY)


@dataclass(frozen=True, slots=True)
class Ambiguous:
    asked: str
    candidates: tuple[str, ...]
    more: int = 0


@dataclass(frozen=True, slots=True)
class NotFound:
    asked: str
    closest: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IsAnItem:
    """No ship, but an item's name fits: item_lookup knows it."""

    asked: str
    item: str


class ShipLibrary:
    """Every ship in one patch, in memory (a few hundred records)."""

    def __init__(
        self,
        ships: Iterable[Ship],
        *,
        version: str,
        as_of: str,
        stale: bool = False,
        concepts: bool = False,
    ) -> None:
        self.ships = tuple(ships)
        self.version = version
        """The Wiki API's patch code, e.g. "4.10.2-LIVE.12881860"."""
        self.as_of = as_of
        """When this copy was downloaded (ISO date)."""
        self.stale = stale
        """True when the current patch couldn't be checked, so this copy may be old."""
        self.concepts = concepts
        """True when the ships still in concept are in it too."""
        # Every request's header finds each ship in the fleet again, and the ships
        # never change, so the answers are kept (audit, 2026-11-03).
        self._found: dict[tuple[str, ...], Ship | Ambiguous | NotFound] = {}

    @property
    def patch(self) -> str:
        """The patch as players say it: "4.10.2"."""
        return self.version.split("-")[0]

    def __len__(self) -> int:
        return len(self.ships)

    def with_concepts(self, concepts: Iterable[Ship]) -> ShipLibrary:
        """This library with ``concepts`` in place of any concept ships it had."""
        ships = [s for s in self.ships if not s.is_concept] + list(concepts)
        return ShipLibrary(
            ships, version=self.version, as_of=self.as_of, stale=self.stale, concepts=True
        )

    def find(self, asked: str) -> Ship | Ambiguous | NotFound:
        query = _query(asked)
        if not query:
            return NotFound(asked, ())
        if (known := self._found.get(query)) is not None:
            return _retold(known, asked)
        if len(self._found) >= REMEMBERED:
            self._found.clear()
        found = self._found[query] = self._search(asked, query)
        return found

    def _search(self, asked: str, query: tuple[str, ...]) -> Ship | Ambiguous | NotFound:
        # Names with every word said score 0.6 or more; the rest score at most 0.5,
        # only to rank the closest names when nothing matches. So the slow part
        # (comparing whole names letter by letter) runs only when nothing matches.
        scored = self._scored(query, near=False)
        if not scored or scored[0][0] < MATCH:
            scored = self._scored(query, near=True)
        best = scored[0][0] if scored else 0.0
        if best < MATCH:
            return NotFound(asked, tuple(s.name for _, s in scored[:CHOICES]))
        tied = [ship for score, ship in scored if score >= best - TIE]
        if len(tied) == 1:
            return tied[0]
        if exact := [ship for ship in tied if query in ship.keys]:
            return exact[0]
        return Ambiguous(
            asked, tuple(s.name for s in tied[:CHOICES]), more=max(0, len(tied) - CHOICES)
        )

    def _scored(self, query: tuple[str, ...], *, near: bool) -> list[tuple[float, Ship]]:
        return sorted(
            (
                (max(_score(query, key, near=near) for key in ship.keys), ship)
                for ship in self.ships
            ),
            key=lambda pair: (-pair[0], len(pair[1].name)),
        )

    def table(self) -> str:
        """Every ship on one line each, for the briefing-table alternative (ADR 15)."""
        lines = ["ship | maker | role | crew | cargo SCU | SCM/max m/s | pledge USD"]
        for ship in sorted(self.ships, key=lambda s: s.name):
            f = ship.facts
            lines.append(
                f"{ship.name} | {ship.maker} | {f.get('role', '')} | {f.get('crew', '')} | "
                f"{f.get('cargo_scu', '')} | {f.get('scm_speed', '')}/{f.get('max_speed', '')} | "
                f"{f.get('pledge_usd', '')}"
            )
        return "\n".join(lines)


def searched(asked: str) -> str:
    """What to ask the unified search for a ship's name: players' names made full
    ("cutty black" is "cutlass black"), then as for an item."""
    words: list[str] = []
    for word in asked.split():
        plain = normalize(word)
        words += ALIASES.get(plain[0], (word,)) if len(plain) == 1 else (word,)
    return search_text(" ".join(words))


def rescued(
    library: ShipLibrary, missed: NotFound, hits: Iterable[Hit]
) -> Ship | IsAnItem | NotFound:
    """A name no ship matched, read again with the search's ``hits``: the one ship
    they name, else an item whose name fits, else the closest across both."""
    hits = list(hits)
    ships = {
        ship.key: ship
        for hit in hits
        if hit.kind == VEHICLES and isinstance(ship := library.find(hit.name), Ship)
    }
    if len(ships) == 1:
        return next(iter(ships.values()))
    query = _query(missed.asked)
    core = tuple(w for w in query if w not in GENERIC) or query

    def fit(name: str) -> float:
        # As for items: "Atlas quantum drive" is the Atlas, a touch less surely.
        words = normalize(name)
        return max(name_score(query, words), name_score(core, words) - 0.02)

    items = sorted(
        (
            (fit(hit.name), hit.name)
            for hit in hits
            if hit.kind in ITEM_KINDS and hit.label not in NOT_ITEMS
        ),
        key=lambda pair: (-pair[0], len(pair[1])),
    )
    if items and items[0][0] >= MATCH:
        return IsAnItem(missed.asked, items[0][1])
    names = [*missed.closest, *(s.name for s in ships.values()), *(name for _, name in items)]
    closest = sorted(dict.fromkeys(names), key=lambda n: (-fit(n), len(n)))
    return replace(missed, closest=tuple(closest[:CHOICES]))


def answer(found: Ship | Ambiguous | NotFound | IsAnItem) -> dict[str, object]:
    """What one name becomes in a look-up result."""
    match found:
        case Ship():
            return dict(found.facts)
        case IsAnItem():
            return {
                "asked": found.asked,
                "is_an_item": found.item,
                "note": "an item, not a ship: use item_lookup with this name",
            }
        case Ambiguous():
            more = f", and {found.more} more" if found.more else ""
            return {
                "asked": found.asked,
                "matches": list(found.candidates),
                "note": f"several ships match{more}: pick the one the player meant, or ask",
            }
        case NotFound():
            return {
                "asked": found.asked,
                "error": "not in the game files or the list of concept ships: "
                "another name, or too newly announced to be listed; if it's a weapon, "
                "armor or ship component, use item_lookup",
                "closest": list(found.closest),
            }


def ships_from_wiki(records: Iterable[Mapping[str, Any]]) -> list[Ship]:
    """One ship per name. The API has extra records for display models and event
    editions ("_Display", "_BIS2950"); keep the one you can buy in the game, then
    the plainest."""
    best: dict[str, Mapping[str, Any]] = {}
    for record in records:
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        if name not in best or _preference(record) < _preference(best[name]):
            best[name] = record
    return [ship_from_wiki(record) for record in best.values()]


def ship_from_wiki(record: Mapping[str, Any]) -> Ship:
    name = str(record.get("name") or record.get("class_name") or "")
    maker = str(_dig(record, "manufacturer", "name") or "")
    facts: dict[str, object] = {
        "name": str(record.get("game_name") or name),
        "maker": maker,
        "kind": _kind(record),
        "role": ", ".join(str(r) for r in (record.get("career"), record.get("role")) if r),
        "size": _english(record.get("size")),
        "crew": _crew(record.get("crew")),
        "cargo_scu": record.get("cargo_capacity"),
        "scm_speed": _dig(record, "speed", "scm"),
        "max_speed": _dig(record, "speed", "max"),
        "quantum_range_gm": _round(_dig(record, "quantum", "quantum_range"), 1e9, 1),
        "hull_hp": record.get("health"),
        "shield_hp": record.get("shield_hp"),
        "shield_type": record.get("shield_face_type"),
        "pilot_dps": _dig(record, "weaponry", "pilot_dps"),
        "missiles": _dig(record, "weaponry", "missiles", "count"),
        "length_m": _dig(record, "dimension", "length"),
        "beam_m": _dig(record, "dimension", "width"),
        "height_m": _dig(record, "dimension", "height"),
        "mass_t": _round(record.get("mass_total"), 1000, 0),
        "claim_minutes": _dig(record, "insurance", "claim_time"),
        "expedite_minutes": _dig(record, "insurance", "expedite_time"),
        "expedite_auec": _dig(record, "insurance", "expedite_cost"),
        "pledge_usd": record.get("msrp"),
        "loaners": _names(record.get("loaner")),
        "buy_in_game": _shops(_dig(record, "uex_prices", "purchase"), "price_buy"),
        "rent_in_game": _shops(_dig(record, "uex_prices", "rental"), "price_rent"),
        "status": _english(record.get("production_status")),
    }
    names = tuple(str(n) for n in (record.get("game_name"),) if n)
    return Ship(
        key=str(record.get("class_name") or name),
        name=name,
        maker=maker,
        facts={k: v for k, v in facts.items() if v not in (None, "", [])},
        names=names,
    )


def concepts_from_uex(records: Iterable[Mapping[str, Any]], known: Iterable[Ship]) -> list[Ship]:
    """The concept ships, leaving out any already in the game files: those win."""
    taken = {key for ship in known for key in ship.keys}
    concepts: dict[str, Ship] = {}
    for record in records:
        ship = concept_from_uex(record)
        if ship.name and not taken & set(ship.keys):
            concepts.setdefault(ship.key, ship)
    return list(concepts.values())


def concept_from_uex(record: Mapping[str, Any]) -> Ship:
    name = str(record.get("name") or "").strip()
    full = str(record.get("name_full") or name)
    roles = [said for flag, said in ROLE_FLAGS.items() if record.get(f"is_{flag}")]
    facts: dict[str, object] = {
        "name": full,
        "maker": str(record.get("company_name") or ""),
        "kind": "ground vehicle" if record.get("is_ground_vehicle") else "spaceship",
        "status": CONCEPT_STATUS,
        "roles": ", ".join(roles),
        "crew": "-".join(str(record.get("crew") or "").split(",")),
        "cargo_scu": _positive(record.get("scu")),
        "length_m": _positive(record.get("length")),
        "beam_m": _positive(record.get("width")),
        "height_m": _positive(record.get("height")),
        "landing_pad": record.get("pad_type"),
    }
    return Ship(
        key=f"{CONCEPT_KEY}{record.get('slug') or name}",
        name=name,
        maker=str(facts["maker"]),
        facts={k: v for k, v in facts.items() if v not in (None, "", [])},
        names=(full,),
    )


def _positive(value: Any) -> float | int | None:
    return value if isinstance(value, int | float) and value > 0 else None


def _query(asked: str) -> tuple[str, ...]:
    words: list[str] = []
    for word in normalize(asked):
        if words and words[-1] == "mk" and word in NUMERALS:
            words.append(NUMERALS[word])
        elif word not in FILLER:
            words += ALIASES.get(word, (word,))
    return tuple(words)


def _retold(found: Ship | Ambiguous | NotFound, asked: str) -> Ship | Ambiguous | NotFound:
    """A remembered answer, for the words this player used."""
    match found:
        case Ship():
            return found
        case Ambiguous() | NotFound():
            return replace(found, asked=asked)


def _score(query: tuple[str, ...], key: tuple[str, ...], *, near: bool = True) -> float:
    if all(any(_same(q, k) for k in key) for q in query):
        # Every word said is in the name; fewer extra words is a closer match.
        return 1.0 - 0.05 * max(0, len(key) - len(query))
    if not near:
        return 0.0
    # A word said that isn't in the name means another ship, however alike the
    # rest looks ("baby Merchantman" isn't the Banu Merchantman). This only ranks
    # the closest names, and stays below MATCH.
    return 0.5 * similarity(" ".join(query), " ".join(key))


@lru_cache(maxsize=65536)
def _same(said: str, word: str) -> bool:
    if said == word:
        return True
    # Forgive a misspelling in a word, never in a model code ("c1", "400i", "f7a").
    if any(c.isdigit() for c in said + word):
        return False
    shorter, longer = sorted((len(said), len(word)))
    # 0.8 alike allows one change in five letters, so longer words can't be close.
    if shorter < 4 or longer - shorter > longer // 5:
        return False
    return similarity(said, word) >= 0.8


def _preference(record: Mapping[str, Any]) -> tuple[bool, bool, int]:
    class_name = str(record.get("class_name") or "")
    buyable = bool(_dig(record, "uex_prices", "purchase"))
    return (not buyable, "display" in class_name.lower(), len(class_name))


def _kind(record: Mapping[str, Any]) -> str:
    if record.get("is_spaceship"):
        return "spaceship"
    if record.get("is_gravlev"):
        return "hover vehicle"
    return "ground vehicle"


def _crew(crew: Any) -> str | None:
    low, high = _dig(crew, "min"), _dig(crew, "max")
    if low is None and high is None:
        return None
    return str(low or high) if low == high or high is None else f"{low or 1}-{high}"


def _shops(rows: Any, price: str) -> list[dict[str, object]]:
    """The three cheapest places in the game to buy or rent it, from UEX via the Wiki."""
    if not isinstance(rows, list):
        return []
    shops: list[dict[str, object]] = []
    for row in cast(list[Any], rows):
        where, cost = _dig(row, "terminal_name"), _dig(row, price)
        if where and isinstance(cost, int | float) and cost > 0:
            shops.append({"where": str(where), "auec": cost})
    return sorted(shops, key=lambda s: cast(float, s["auec"]))[:3]


def _names(rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return []
    return [str(name) for row in cast(list[Any], rows) if (name := _dig(row, "name"))]


def _english(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = _dig(value, "en_EN")
    return str(value) if value else None


def _round(value: Any, unit: float, places: int) -> float | int | None:
    if not isinstance(value, int | float):
        return None
    scaled = round(value / unit, places)
    return int(scaled) if places == 0 else scaled


def _dig(record: Any, *path: str) -> Any:
    """``record[path[0]][path[1]]...``, or None wherever a step is missing."""
    value: Any = record
    for step in path:
        if not isinstance(value, Mapping):
            return None
        value = cast(Mapping[str, Any], value).get(step)
    return value
