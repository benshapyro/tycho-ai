"""Where to refine an ore, and where to buy a ship (#152): two more look-ups from UEX,
sharing the trade look-ups' data (``Trade``). Both satisfy ``brain.lookups.Lookup``.

UEX's refinery yields are each refinery's bonus or penalty, in percent, for each ore;
its methods are rated 1 to 3 for yield, cost and speed. Its ship prices are where
each ship is sold in the game, in aUEC.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from tycho.brain.lookups import LookupResult, failure
from tycho.knowledge.trade import Trade, TradeError
from tycho.knowledge.trade_lookup import SOURCE
from tycho.knowledge.wiki import WikiError
from tycho.text import name_score, normalize

log = logging.getLogger(__name__)

SECONDS = 3.0
"""Measured at most 0.9 s (8 November 2026, idle and with the game running, #150)."""
SHOWN = 5
SHIP_MATCH = 0.75
"""How close a ship's name must be to what was said."""

NOT_SHIP_WORDS = frozenset(
    {
        *("a", "an", "the", "my", "in", "game", "ingame", "ship", "new", "one"),
        # Makers: UEX's ship names leave them out ("Prospector", not "MISC Prospector").
        *("misc", "rsi", "drake", "anvil", "aegis", "origin", "crusader", "argo", "esperia"),
        *("consolidated", "outland", "tumbril", "banu", "aopoa", "kruger", "greycat"),
        *("mirai", "gatac", "roberts", "space", "industries", "musashi", "industrial"),
    }
)
"""Never part of what's matched: filler, and the makers' names. Short words like
"misc" and "a" are too near words in real names ("Mis", "Hull A") to tell apart."""

CREDIT = "In what you say aloud, credit UEX once, in a few words."

REFINING = f"""\
Where to refine an ore in Star Citizen, from UEX: each refinery's yield bonus or \
penalty for it, in percent, best first, and the refining methods rated 1-3 for \
yield, cost and speed. Pass the ore as the player said it ("quantanium", "raw \
laranite"), and a place if they named one (a system, planet or station). For \
selling the refined material, use commodity_prices. {CREDIT} A result with \
not_found: say briefly what wasn't found."""

SHIPS = f"""\
Where to buy a ship in Star Citizen with in-game money (aUEC), from UEX: the shops \
that sell it and their prices, cheapest first. Pass the ship as the player said it. \
Use it for "where can I buy a Cutlass" and "how much is a Prospector in game"; for \
a ship's specs use ship_lookup. Pledge prices in real money aren't here. {CREDIT} \
A result with not_found: say briefly what wasn't found."""


class _UexLookup:
    seconds = SECONDS
    credit = "UEX"

    def __init__(self, trade: Trade) -> None:
        self._trade = trade

    async def prepare(self) -> None:
        """The trade look-ups load the shared lists; this only makes sure."""
        if not self._trade.ready:
            try:
                await self._trade.load()
            except WikiError as e:
                log.warning("UEX lists didn't load: %s", e)

    def _result(self, body: Mapping[str, object]) -> LookupResult:
        return LookupResult(
            json.dumps({"source": SOURCE, **body}, ensure_ascii=False, separators=(",", ":"))
        )


class RefiningLookup(_UexLookup):
    @property
    def name(self) -> str:
        return "refining"

    @property
    def description(self) -> str:
        return REFINING

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "ore": {"type": "string", "description": "As the player said it."},
                "place": {
                    "type": "string",
                    "description": "Where, if the player named a place; otherwise empty.",
                },
            },
            "required": ["ore", "place"],
            "additionalProperties": False,
        }

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        await self.prepare()
        if not self._trade.ready:
            return failure("the UEX data isn't loaded; answer without it, and say so")
        trade = self._trade
        said = str(arguments.get("ore") or "").strip()
        asked = str(arguments.get("place") or "").strip()
        try:
            # Refineries take ore: "laranite" is Laranite (Raw) here.
            commodity, _ = trade.commodity_match(said if _says_raw(said) else f"raw {said}")
            place = trade.place(asked) if asked else None
            yields = await trade.refinery_yields()
            methods = await trade.refining_methods()
        except TradeError as e:
            return self._result({"not_found": str(e)})
        except WikiError as e:
            return failure(f"UEX didn't answer ({e}); answer without it")
        rows = [r for r in yields if int(r.get("id_commodity") or 0) == commodity.id]
        if place is not None:
            rows = [r for r in rows if trade.in_place(r, place)]
        rows.sort(key=lambda r: -float(r.get("value") or 0))
        body: dict[str, object] = {
            "ore": commodity.name,
            "place": place.value if place else None,
            "refineries": [
                {**trade.where(int(r.get("id_terminal") or 0)), "yield_percent": r.get("value")}
                for r in rows[:SHOWN]
            ],
            "methods": [
                {
                    "name": m.get("name"),
                    "yield": m.get("rating_yield"),
                    "cost": m.get("rating_cost"),
                    "speed": m.get("rating_speed"),
                }
                for m in sorted(methods, key=lambda m: -int(m.get("rating_yield") or 0))
            ],
        }
        if not rows:
            at = f" at {place.value}" if place else ""
            what = "" if commodity.raw else " (it isn't an ore)"
            body["note"] = f"UEX has no refinery yields for {commodity.name}{what}{at}"
        return self._result(body)


class ShipPurchases(_UexLookup):
    @property
    def name(self) -> str:
        return "ship_purchases"

    @property
    def description(self) -> str:
        return SHIPS

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {"ship": {"type": "string", "description": "As the player said it."}},
            "required": ["ship"],
            "additionalProperties": False,
        }

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        said = str(arguments.get("ship") or "").strip()
        if not said:
            return self._result({"not_found": "no ship: ask which one"})
        await self.prepare()
        try:
            prices = await self._trade.ship_prices()
        except WikiError as e:
            return failure(f"UEX didn't answer ({e}); answer without it")
        names = sorted({str(p.get("vehicle_name") or "") for p in prices} - {""})
        words = _ship_words(said, names)
        # Best first; among names as good, the first alphabetically ("a Cutlass" is the
        # Cutlass Black), with the others named.
        scored = sorted((-name_score(words, normalize(n)), n) for n in names)
        if not scored or -scored[0][0] < SHIP_MATCH:
            closest = ", ".join(n for _, n in scored[:3])
            return self._result(
                {"not_found": f"no ship called {said!r} is sold in game; closest: {closest}"}
            )
        best, ship = scored[0]
        others = [n for score, n in scored[1:] if score <= best + 0.03][:4]
        rows = sorted(
            (p for p in prices if p.get("vehicle_name") == ship and _priced(p)),
            key=lambda p: float(p.get("price_buy") or 0),
        )
        body: dict[str, object] = {
            "ship": ship,
            "shops": [_shop(self._trade, p) for p in rows[:SHOWN]],
        }
        if others:
            body["also_matches"] = others
        if normalize(ship) != words:
            # Not quite what was said: "Cutlass Blue" when only the Black is sold.
            body["nearest"] = f"UEX lists no {' '.join(words)!r} exactly; this is the nearest"
        if not rows:
            body["note"] = f"UEX has no in-game price for the {ship} yet"
        return self._result(body)


def _ship_words(said: str, names: list[str]) -> list[str]:
    """What was said, less the maker ("MISC Prospector": UEX names have none) and
    filler ("the Cutlass"). Every other word stays, so "Cutlass Blue" isn't taken
    for any Cutlass. If nothing would be left, everything said stays."""
    kept = [w for w in normalize(said) if w not in NOT_SHIP_WORDS]
    return kept or normalize(said)


def _priced(row: Mapping[str, Any]) -> bool:
    price = row.get("price_buy")
    return isinstance(price, int | float) and price > 0


def _says_raw(said: str) -> bool:
    return any(w in {"raw", "ore", "unrefined"} for w in said.lower().split())


def _shop(trade: Trade, row: Mapping[str, Any]) -> dict[str, object]:
    where = trade.where(int(row.get("id_terminal") or 0))
    if where["where"].startswith("terminal "):  # not a commodity terminal: UEX's name
        where["where"] = str(row.get("terminal_name") or where["where"])
    return {**where, "auec": row.get("price_buy")}
