"""The trade look-ups the brain can call (ADR 15): commodity prices and trade routes,
from UEX. Both satisfy ``brain.lookups.Lookup`` and share one ``Trade``.

UEX's prices are reported by players, so a result carries how old each price is.
Tycho says the source once and the age only when it's old (Ben, 2026-11-02).
"""

import asyncio
import json
import logging
from collections.abc import Coroutine, Mapping
from typing import Any

from tycho.brain.lookups import Lookup, LookupResult, failure
from tycho.knowledge.trade import (
    MAX_ORIGINS,
    Commodity,
    Place,
    Trade,
    TradeError,
    hours_ago,
    top,
)
from tycho.knowledge.wiki import WikiError, WikiSource

log = logging.getLogger(__name__)

SOURCE = "UEX (uexcorp.space), prices reported by players"
SECONDS = 4.0
"""A route from a station asks UEX about each of its terminals at once. Measured on Ben's
PC (8 November 2026, idle and with the game running): at most 0.9 s, so 4 s keeps
room for UEX's slow moments while a UEX that hangs fails a second sooner (#150). The
4.6 s answer in the 6 November play session was the Obituary station question, which
``PART_SECONDS`` now cuts short: don't raise this to fit it (#231)."""
PART_SECONDS = 2.5
"""How long a station's terminals are waited for, when asked one by one: the routes
from those that answered are given, so one slow terminal can't use up ``SECONDS``
(#150, the Obituary question in the play session)."""
ROUTES_CAP = 500
"""UEX returns at most this many routes for one commodity: Scrap's list stopped at 500
(measured 6 November 2026; others had 120 to 320)."""
SPEAKING = (
    "In what you say aloud, credit UEX once, in a few words, even when the details go "
    "on a card (show_card's say); say how old a price is only when "
    "its hours_ago is over 6. Prices are aUEC per SCU. A result with also_matches, "
    "also_places, also_starts or also_ends is for the likeliest match: answer for it and "
    "name the others in a few words; don't look up again to check. A result with "
    "not_found: say briefly what wasn't found, and the closest names if it gives any."
)

PRICES = f"""\
Live Star Citizen commodity prices from UEX: where to sell a commodity for the \
most, where to buy it cheapest, and the price at a particular place. Pass the \
commodity as the player said it ("quantanium", "raw laranite"), and a place if \
they named one (a city, station, outpost, moon, planet or system). Don't use it \
for trade routes (use trade_routes) or ship prices. {SPEAKING}"""

ROUTES = f"""\
Live Star Citizen trade routes from UEX: what to buy at a place and where to \
sell it, ranked by profit per SCU. Pass where the player is starting from (a \
city, station, outpost or planet), and if they said them, a commodity, where \
they're going, and their cargo space in SCU (use ship_lookup's cargo_scu for a \
named ship). With a commodity and no start, it finds that commodity's best routes \
from anywhere: don't ask where they are first. Don't ask for cargo space just for \
this: without it, give profit per SCU. {SPEAKING}"""


class _TradeLookup:
    seconds = SECONDS
    credit = "UEX"
    """Said with any answer from it, if Claude didn't (Ben: credit UEX once)."""

    def __init__(self, trade: Trade) -> None:
        self._trade = trade
        self._problem = "the trade data is still loading"

    async def close(self) -> None:
        await self._trade.close()

    async def prepare(self) -> None:
        """Load the commodity and terminal lists once, shared by both look-ups. Never raises."""
        if self._trade.ready:
            return
        try:
            await self._trade.load()
        except WikiError as e:
            self._problem = f"UEX can't be reached ({e})"
            log.warning("Trade look-ups are off for now: %s", e)

    async def _ready(self) -> str | None:
        """Why the data isn't there, after one more try; None when it is."""
        if not self._trade.ready:
            await self.prepare()
        return None if self._trade.ready else self._problem

    def _result(self, body: Mapping[str, object]) -> LookupResult:
        return LookupResult(
            json.dumps({"source": SOURCE, **body}, ensure_ascii=False, separators=(",", ":"))
        )

    def _miss(self, problem: str) -> LookupResult:
        """A name UEX doesn't have: an answer, not an error (#147). Claude retries an
        error, and a retry costs a second; this one it says to the player."""
        return self._result({"not_found": problem})


class CommodityPrices(_TradeLookup):
    @property
    def name(self) -> str:
        return "commodity_prices"

    @property
    def description(self) -> str:
        return PRICES

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "commodity": {"type": "string", "description": "As the player said it."},
                "place": {
                    "type": "string",
                    "description": "Where, if the player named a place; otherwise empty.",
                },
            },
            "required": ["commodity", "place"],
            "additionalProperties": False,
        }

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        if problem := await self._ready():
            return failure(f"{problem}; answer without live prices, and say so")
        trade = self._trade
        assumed: dict[str, object] = {}
        try:
            commodity, others = trade.commodity_match(str(arguments.get("commodity") or ""))
            if others:
                assumed["also_matches"] = list(others)
            asked = str(arguments.get("place") or "").strip()
            place = None
            if asked:
                place, near = trade.place_match(asked)
                if near:
                    assumed["also_places"] = list(near)
            rows = await trade.prices(commodity)
        except TradeError as e:
            return self._miss(str(e))
        except WikiError as e:
            return failure(f"UEX didn't answer ({e}); answer without live prices")
        if place is not None:
            rows = [r for r in rows if trade.in_place(r, place)]
        body: dict[str, object] = {
            "commodity": commodity.name,
            "place": place.value if place else None,
            **assumed,
            "sell_highest": [
                self._row(r, "price_sell", "scu_sell") for r in top(rows, "price_sell")
            ],
            "buy_cheapest": [
                self._row(r, "price_buy", "scu_buy") for r in top(rows, "price_buy", cheapest=True)
            ],
        }
        if not body["sell_highest"] and not body["buy_cheapest"]:
            at = f" at {place.value}" if place else ""
            body["note"] = f"UEX has no {commodity.name} prices{at}"
        return self._result(body)

    def _row(self, row: Mapping[str, Any], price: str, scu: str) -> dict[str, object]:
        out: dict[str, object] = {
            **self._trade.where(int(row.get("id_terminal") or 0)),
            "auec": row.get(price),
            "hours_ago": hours_ago(row.get("date_modified")),
        }
        if isinstance(amount := row.get(scu), int | float) and amount > 0:
            out["scu"] = amount
        return out


class TradeRoutes(_TradeLookup):
    @property
    def name(self) -> str:
        return "trade_routes"

    @property
    def description(self) -> str:
        return ROUTES

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "Where the player starts from, if they said; otherwise empty.",
                },
                "commodity": {"type": "string", "description": "If named; otherwise empty."},
                "end": {"type": "string", "description": "If named; otherwise empty."},
                "cargo_scu": {
                    "type": "integer",
                    "description": "The player's cargo space, if known; otherwise 0.",
                },
            },
            "required": ["start", "commodity", "end", "cargo_scu"],
            "additionalProperties": False,
        }

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        if problem := await self._ready():
            return failure(f"{problem}; answer without live routes, and say so")
        trade = self._trade
        cargo = arguments.get("cargo_scu")
        cargo = cargo if isinstance(cargo, int) and cargo > 0 else 0
        assumed: dict[str, object] = {}
        try:
            start_said = str(arguments.get("start") or "").strip()
            commodity_said = str(arguments.get("commodity") or "").strip()
            if not start_said and not commodity_said:
                return self._miss("no start or commodity: ask where they're starting, or what")
            start = None
            if start_said:
                start, near = trade.place_match(start_said)
                if near:
                    assumed["also_starts"] = list(near)
            end_said = str(arguments.get("end") or "").strip()
            end = None
            if end_said:
                end, far = trade.place_match(end_said)
                if far:
                    assumed["also_ends"] = list(far)
            commodity = None
            if commodity_said:
                commodity, others = trade.commodity_match(commodity_said)
                if others:
                    assumed["also_matches"] = list(others)
            capped = False
            if start is not None and self._narrow(start):
                # A planet, an orbit or a few terminals: ask from there, for just the
                # commodity if there is one. Small lists, in one round trip (#150).
                try:
                    rows, capped = await self._from(start, commodity)
                    wide = False
                except WikiError:
                    if commodity is None:
                        raise
                    # Should UEX ever refuse a start and a commodity together, the
                    # commodity's own list still answers (review of #211).
                    rows = await trade.routes({"id_commodity": commodity.id})
                    capped, wide = len(rows) >= ROUTES_CAP, True
            elif commodity is not None:
                # One request for every route of one commodity (#147), kept below to the
                # start if there is one: a whole system ("gold in Pyro"), which a few of
                # its terminals asked one by one would miss.
                rows = await trade.routes({"id_commodity": commodity.id})
                capped, wide = len(rows) >= ROUTES_CAP, True
            elif start is not None:
                rows, capped = await self._from(start, None)
                wide = False
            else:
                rows, wide = [], False
        except TradeError as e:
            return self._miss(str(e))
        except WikiError as e:
            return failure(f"UEX didn't answer ({e}); answer without live routes")

        def kept(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if end is not None:
                rows = [r for r in rows if trade.in_place(r, end, "_destination")]
            if commodity is not None:
                rows = [r for r in rows if int(r.get("id_commodity") or 0) == commodity.id]
            if start is not None:
                rows = [r for r in rows if trade.in_place(r, start, "_origin")]
            return rows

        rows = kept(rows)
        if not rows and capped and start is not None and commodity is not None:
            # UEX cut a list at its cap (#171): ask the other way round. From the start
            # itself after the commodity's list, or the commodity's list after the start's
            # (should UEX ever not narrow the start's routes to the commodity).
            try:
                rows = kept(
                    (await self._from(start, commodity))[0]
                    if wide
                    else await trade.routes({"id_commodity": commodity.id})
                )
            except TradeError, WikiError:
                rows = []
        best = sorted(rows, key=lambda r: -_profit(r, cargo))[:5]
        body: dict[str, object] = {
            "start": start.value if start is not None else "anywhere",
            "commodity": commodity.name if commodity is not None else None,
            "cargo_scu": cargo or None,
            **assumed,
            "routes": [self._route(r, cargo) for r in best],
        }
        if not best:
            where = f"from {start.value}" if start is not None else "anywhere"
            body["note"] = f"UEX has no matching routes {where}"
        return self._result(body)

    def _narrow(self, start: Place) -> bool:
        """Whether routes from ``start`` are best asked from the start itself: UEX starts
        routes from a planet or an orbit, and a station, city or outpost has only a few
        terminals. A system or a moon has too many to ask one by one."""
        if start.id_field in ("id_planet", "id_orbit") and start.id:
            return True
        return 0 < len(self._trade.terminals_at(start)) <= MAX_ORIGINS

    async def _from(
        self, start: Place, commodity: Commodity | None
    ) -> tuple[list[dict[str, Any]], bool]:
        """The routes from ``start`` (of ``commodity`` only, if there is one), and whether
        a list UEX gave was cut at its cap. UEX starts routes from a terminal, a planet or
        an orbit: anything else is asked terminal by terminal, at once, and the terminals
        that haven't answered after ``PART_SECONDS`` are left out."""
        only: dict[str, int] = {"id_commodity": commodity.id} if commodity is not None else {}
        if start.id_field in ("id_planet", "id_orbit") and start.id:
            rows = await self._trade.routes({f"{start.id_field}_origin": start.id, **only})
            return rows, len(rows) >= ROUTES_CAP
        terminals = self._trade.terminals_at(start)[:MAX_ORIGINS]
        if not terminals:
            raise TradeError(f"UEX lists no trade terminals at {start.value}")
        lists = await _answered(
            [self._trade.routes({"id_terminal_origin": t, **only}) for t in terminals],
            PART_SECONDS,
            start.value,
        )
        return [row for rows in lists for row in rows], any(
            len(rows) >= ROUTES_CAP for rows in lists
        )

    def _route(self, row: Mapping[str, Any], cargo: int) -> dict[str, object]:
        buy, sell = (
            self._trade.where(int(row.get("id_terminal_origin") or 0)),
            self._trade.where(int(row.get("id_terminal_destination") or 0)),
        )
        out: dict[str, object] = {
            "commodity": row.get("commodity_name"),
            "buy_at": buy["where"],
            "sell_at": sell["where"],
            "sell_around": sell["around"],
            "buy_auec": row.get("price_origin"),
            "sell_auec": row.get("price_destination"),
            "profit_per_scu": _per_scu(row),
            "scu_available": row.get("scu_reachable") or row.get("scu_origin"),
            "distance": row.get("distance"),
            "hours_ago": hours_ago(row.get("date_added")),
        }
        if cargo:
            out["profit_for_cargo"] = _profit(row, cargo)
        return out


async def _answered[T](
    requests: list[Coroutine[Any, Any, T]], seconds: float, where: str
) -> list[T]:
    """The answers to ``requests``, made at once, from those that answered within
    ``seconds``. While none has, it waits on for the first (the look-up's own time
    limit still stops it). Raises ``WikiError`` only when every request failed."""
    tasks = [asyncio.ensure_future(r) for r in requests]
    try:
        done, waiting = await asyncio.wait(tasks, timeout=seconds)
        while waiting and not any(t.exception() is None for t in done):
            more, waiting = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            done |= more
    finally:
        for task in tasks:
            task.cancel()
    answers = [t.result() for t in tasks if t in done and t.exception() is None]
    problems = [e for t in tasks if t in done and (e := t.exception()) is not None]
    if not answers:
        problem = problems[0] if problems else None
        raise WikiError(str(problem or f"no terminal at {where} answered")) from problem
    if waiting or problems:
        log.info(
            "Routes from %s: %d of %d terminals answered in time",
            where,
            len(answers),
            len(tasks),
        )
    return answers


def _per_scu(row: Mapping[str, Any]) -> float:
    buy, sell = row.get("price_origin"), row.get("price_destination")
    if isinstance(buy, int | float) and isinstance(sell, int | float):
        return round(sell - buy, 2)
    return 0.0


def _profit(row: Mapping[str, Any], cargo: int) -> float:
    """Profit for the cargo (as much as is on sale), or per SCU without one."""
    if not cargo:
        return _per_scu(row)
    available = row.get("scu_reachable") or row.get("scu_origin") or 0
    scu = min(cargo, int(available)) if isinstance(available, int | float) and available else cargo
    return round(_per_scu(row) * scu)


def trade_lookups(source: WikiSource) -> list[Lookup]:
    """The UEX look-ups, sharing one set of its data: prices, routes, refining (#152)
    and where to buy a ship."""
    from tycho.knowledge.refining import RefiningLookup, ShipPurchases

    trade = Trade(source)
    return [CommodityPrices(trade), TradeRoutes(trade), RefiningLookup(trade), ShipPurchases(trade)]
