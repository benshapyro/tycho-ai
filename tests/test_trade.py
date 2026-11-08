"""Trade (ADR 15): commodity and place names, prices and routes from UEX, and the
two look-ups the brain calls."""

import asyncio
import json
from typing import Any

import pytest

from tests.fakes.fake_uex import NOW, FakeTradeUex
from tycho.knowledge import WikiError
from tycho.knowledge.trade import FRESH_SECONDS, Trade, TradeError, hours_ago
from tycho.knowledge.trade_lookup import CommodityPrices, TradeRoutes, trade_lookups


def _trade(uex: FakeTradeUex | None = None, clock: Any = None) -> Trade:
    trade = Trade(uex or FakeTradeUex(), **({"clock": clock} if clock else {}))
    asyncio.run(trade.load())
    return trade


# --- Names ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("laranite", "Laranite"),  # not Caranite, which is one letter off
        ("Laranite", "Laranite"),
        ("raw laranite", "Laranite (Raw)"),
        ("laranite ore", "Laranite (Raw)"),
        ("quantanium", "Quantainium"),  # misspelled, as speech-to-text writes it
        ("quant", "Quantainium"),  # the start of the name
        ("QUAN", "Quantainium"),  # the code
        ("medical supplies", "Medical Supplies"),
        ("gold", "Gold"),  # not "Gold or Gold (Ore)?": UEX calls the ore that (play session)
        ("refined gold", "Gold"),
        ("gold ore", "Gold (Ore)"),
        ("raw gold", "Gold (Ore)"),
    ],
)
def test_commodities_are_found_by_what_players_call_them(said: str, expected: str) -> None:
    assert _trade().commodity(said).name == expected


def test_an_unknown_commodity_offers_the_closest() -> None:
    with pytest.raises(TradeError, match="closest: "):
        _trade().commodity("unobtainium")


@pytest.mark.parametrize(
    ("said", "field", "value"),
    [
        ("Area 18", "city_name", "Area 18"),
        ("Area18", "city_name", "Area 18"),
        ("A18", "city_name", "Area 18"),
        ("New Babbage", "city_name", "New Babbage"),
        ("NB", "city_name", "New Babbage"),
        ("Hurston", "planet_name", "Hurston"),  # the planet, not its Lagrange points
        ("Pyro", "star_system_name", "Pyro"),  # the system, not Pyro Gateway
        ("Pyro Gateway", "space_station_name", "Pyro Gateway"),
        ("ruin", "space_station_name", "Ruin Station"),  # part of a name
        ("Lorvile", "city_name", "Lorville"),  # misspelled
    ],
)
def test_places_are_found_most_sensibly(said: str, field: str, value: str) -> None:
    place = _trade().place(said)
    assert (place.field, place.value) == (field, value)


def test_a_place_that_fits_a_few_is_the_likeliest_with_the_others_named() -> None:
    """#147: a best guess, not a question back, which cost a call to Claude."""
    place, others = _trade().place_match("Lagrange")
    assert "Lagrange" in place.value
    assert others
    assert all("Lagrange" in o for o in others)


def test_a_place_that_fits_many_asks_which(monkeypatch: pytest.MonkeyPatch) -> None:
    import tycho.knowledge.trade

    monkeypatch.setattr(tycho.knowledge.trade, "GUESS_AMONG", 1)
    with pytest.raises(TradeError, match="name one"):
        _trade().place("Lagrange")
    with pytest.raises(TradeError, match="no place called"):
        _trade().place("nowhere special")


# --- Fetching ------------------------------------------------------------------------------------


def test_a_fetched_list_is_reused_for_a_while_then_fetched_again() -> None:
    uex, now = FakeTradeUex(), [0.0]
    trade = _trade(uex, clock=lambda: now[0])
    laranite = trade.commodity("laranite")

    async def twice() -> None:
        await trade.prices(laranite)
        now[0] += FRESH_SECONDS - 1
        await trade.prices(laranite)
        now[0] += 2
        await trade.prices(laranite)

    asyncio.run(twice())
    assert [c[0] for c in uex.calls].count("/commodities_prices") == 2


def test_raw_ore_is_priced_at_refineries() -> None:
    uex = FakeTradeUex()
    trade = _trade(uex)
    rows = asyncio.run(trade.prices(trade.commodity("raw laranite")))
    assert rows[0]["price_sell"] == 1281
    assert uex.calls[-1][0] == "/commodities_raw_prices"


def test_the_lists_load_once_however_many_look_ups_ask() -> None:
    uex = FakeTradeUex()
    prices, routes, *_ = trade_lookups(uex)

    async def both() -> None:
        await asyncio.gather(prices.prepare(), routes.prepare())

    asyncio.run(both())
    assert [c[0] for c in uex.calls].count("/terminals") == 1


def test_hours_ago() -> None:
    assert hours_ago(NOW - 5400, now=NOW) == 1.5
    assert hours_ago(0, now=NOW) is None
    assert hours_ago(None, now=NOW) is None


# --- The look-ups ---------------------------------------------------------------------------------


def _run(lookup: Any, arguments: dict[str, Any]) -> tuple[bool, Any]:
    async def go() -> Any:
        await lookup.prepare()
        return await lookup.run(arguments)

    result = asyncio.run(go())
    return result.is_error, json.loads(result.content)


def _prices() -> CommodityPrices:
    prices, *_ = trade_lookups(FakeTradeUex())
    assert isinstance(prices, CommodityPrices)
    return prices


def _routes(uex: FakeTradeUex | None = None) -> TradeRoutes:
    _, routes, *_ = trade_lookups(uex or FakeTradeUex())
    assert isinstance(routes, TradeRoutes)
    return routes


def test_prices_list_where_to_sell_highest_and_buy_cheapest() -> None:
    error, result = _run(_prices(), {"commodity": "laranite", "place": ""})
    assert not error
    assert result["source"].startswith("UEX")
    assert result["commodity"] == "Laranite"
    assert [s["where"] for s in result["sell_highest"]] == ["New Babbage", "Pyro Gateway", "Area18"]
    best = result["sell_highest"][0]
    assert best["auec"] == 9100
    assert best["around"] == "MicroTech, Stanton"
    assert best["hours_ago"] > 6  # old enough that Tycho should say so
    assert result["sell_highest"][2]["scu"] == 1784
    assert [b["where"] for b in result["buy_cheapest"]] == ["Lorville"]


def test_prices_at_one_place() -> None:
    _, result = _run(_prices(), {"commodity": "medical supplies", "place": "Pyro"})
    assert result["place"] == "Pyro"
    assert [s["where"] for s in result["sell_highest"]] == ["Ruin Station"]
    _, nothing = _run(_prices(), {"commodity": "quantainium", "place": "New Babbage"})
    assert nothing["note"] == "UEX has no Quantainium prices at New Babbage"


def test_routes_from_a_planet_ranked_by_profit_per_scu() -> None:
    error, result = _run(
        _routes(), {"start": "Hurston", "commodity": "", "end": "", "cargo_scu": 0}
    )
    assert not error
    assert result["start"] == "Hurston"
    assert [(r["commodity"], r["sell_at"]) for r in result["routes"]] == [
        ("Laranite", "New Babbage"),
        ("Laranite", "Area18"),
        ("Medical Supplies", "New Babbage"),
    ]
    assert result["routes"][0]["profit_per_scu"] == 1559
    assert "profit_for_cargo" not in result["routes"][0]


def test_routes_with_cargo_count_only_what_is_on_sale() -> None:
    # 1,000 SCU of cargo, but only 608 SCU of laranite on sale at Lorville.
    _, result = _run(_routes(), {"start": "Hurston", "commodity": "", "end": "", "cargo_scu": 1000})
    assert [r["profit_for_cargo"] for r in result["routes"]] == [
        1559 * 608,  # laranite to New Babbage
        959 * 608,  # laranite to Area 18
        300 * 1000,  # medical supplies: plenty on sale, but a thin margin
    ]


def test_routes_from_a_station_ask_about_its_terminals() -> None:
    uex = FakeTradeUex()
    _, routes, *_ = trade_lookups(uex)
    _, result = _run(routes, {"start": "ARC-L1", "commodity": "", "end": "", "cargo_scu": 0})
    assert [r["commodity"] for r in result["routes"]] == ["Quantainium"]
    assert ("/commodities_routes", {"id_terminal_origin": 1}) in uex.calls


def test_routes_to_one_place_and_of_one_commodity() -> None:
    _, result = _run(
        _routes(), {"start": "Hurston", "commodity": "laranite", "end": "Area 18", "cargo_scu": 46}
    )
    (only,) = result["routes"]
    assert (only["commodity"], only["sell_at"]) == ("Laranite", "Area18")
    assert only["profit_for_cargo"] == 959 * 46


def test_a_name_that_misses_is_an_answer_not_an_error() -> None:
    """#147: Claude retries an error, three times over in the play session. A miss
    says what wasn't found and the closest names, for Claude to tell the player."""
    error, result = _run(_prices(), {"commodity": "unobtainium", "place": ""})
    assert not error
    assert "closest" in result["not_found"]
    assert "answer" not in result  # data only: how to say it is in the description
    error, result = _run(
        _routes(), {"start": "nowhere", "commodity": "", "end": "", "cargo_scu": 0}
    )
    assert not error
    assert "no place called" in result["not_found"]


def test_routes_for_a_commodity_from_anywhere() -> None:
    """Play session: "best route for buying and selling refined gold?" was answered
    "Where are you starting from?". With a commodity, no start is needed."""
    uex = FakeTradeUex()
    error, result = _run(
        _routes(uex), {"start": "", "commodity": "laranite", "end": "", "cargo_scu": 0}
    )
    assert not error
    assert result["start"] == "anywhere"
    assert result["commodity"] == "Laranite"
    assert [r["sell_at"] for r in result["routes"]] == ["New Babbage", "Area18"]
    assert ("/commodities_routes", {"id_commodity": 47}) in uex.calls


def test_routes_with_neither_a_start_nor_a_commodity_say_what_to_ask() -> None:
    error, result = _run(_routes(), {"start": "", "commodity": "", "end": "", "cargo_scu": 0})
    assert not error
    assert "ask where" in result["not_found"]


def test_an_assumed_match_names_the_others() -> None:
    error, result = _run(_prices(), {"commodity": "laranite", "place": "Lagrange"})
    assert not error
    assert result["also_places"]


def test_without_uex_it_says_so_and_tries_again_next_time() -> None:
    uex = FakeTradeUex()
    uex.offline = True
    prices, *_ = trade_lookups(uex)
    error, result = _run(prices, {"commodity": "laranite", "place": ""})
    assert error
    assert "can't be reached" in result["error"]
    uex.offline = False
    assert not asyncio.run(prices.run({"commodity": "laranite", "place": ""})).is_error


def test_the_tool_schemas_suit_strict_tool_use() -> None:
    for lookup in trade_lookups(FakeTradeUex()):
        schema = lookup.input_schema
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])  # type: ignore[arg-type]
        assert "UEX" in lookup.description


def test_a_start_and_a_commodity_ask_uex_once_for_both() -> None:
    """#150: a start and a commodity are one small request, not the commodity's whole
    list (up to 500 routes) first and then the start's."""
    uex = FakeTradeUex()
    _, result = _run(
        _routes(uex), {"start": "Hurston", "commodity": "laranite", "end": "", "cargo_scu": 0}
    )
    assert [(r["buy_at"], r["sell_at"]) for r in result["routes"]] == [
        ("Lorville", "New Babbage"),
        ("Lorville", "Area18"),
    ]
    routes = [query for path, query in uex.calls if path == "/commodities_routes"]
    assert routes == [{"id_planet_origin": 1, "id_commodity": 47}]


def test_a_station_and_a_commodity_ask_its_terminals_once() -> None:
    """The play session's "I'm in Pyro at Obituary" (#150): before, the commodity's
    whole list was asked first, and the station's terminals only after it came back
    capped. Now it is the station's terminals, for that commodity, in one round."""
    uex = FakeTradeUex()
    _, result = _run(
        _routes(uex), {"start": "ARC-L1", "commodity": "quantanium", "end": "", "cargo_scu": 0}
    )
    assert [r["sell_at"] for r in result["routes"]] == ["Lorville"]
    routes = [query for path, query in uex.calls if path == "/commodities_routes"]
    assert routes == [{"id_terminal_origin": 1, "id_commodity": 58}]


def test_a_slow_terminal_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """#150: one terminal that doesn't answer mustn't use up the look-up's time: the
    routes from those that did answer are given."""
    import tycho.knowledge.trade_lookup

    uex = FakeTradeUex()
    real_get = uex.get

    async def slow_at_area18(path: str, params: dict[str, int] | None = None) -> Any:
        if path == "/commodities_routes" and (params or {}).get("id_terminal_origin") == 12:
            await asyncio.sleep(10)
        return await real_get(path, params)

    monkeypatch.setattr(uex, "get", slow_at_area18)
    monkeypatch.setattr(tycho.knowledge.trade_lookup, "PART_SECONDS", 0.05)
    monkeypatch.setattr(Trade, "terminals_at", _arc_l1_and_area18)
    error, result = _run(
        _routes(uex), {"start": "ARC-L1", "commodity": "", "end": "", "cargo_scu": 0}
    )
    assert not error
    assert [r["commodity"] for r in result["routes"]] == ["Quantainium"]


def _arc_l1_and_area18(self: Trade, place: object) -> list[int]:
    """ARC-L1 and Area 18 as if in one place, for the slow-terminal test."""
    return [1, 12]


def test_with_no_terminal_answered_it_waits_for_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review of #211: with none answered by PART_SECONDS, giving up would fail a look-up
    that still had time left. It waits on for the first answer instead."""
    import tycho.knowledge.trade_lookup

    uex = FakeTradeUex()
    real_get = uex.get

    async def slow(path: str, params: dict[str, int] | None = None) -> Any:
        if path == "/commodities_routes":
            await asyncio.sleep(0.2)
        return await real_get(path, params)

    monkeypatch.setattr(uex, "get", slow)
    monkeypatch.setattr(tycho.knowledge.trade_lookup, "PART_SECONDS", 0.05)
    error, result = _run(
        _routes(uex), {"start": "ARC-L1", "commodity": "", "end": "", "cargo_scu": 0}
    )
    assert not error
    assert [r["commodity"] for r in result["routes"]] == ["Quantainium"]


def test_terminals_that_fail_are_left_out_and_all_failing_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uex = FakeTradeUex()
    real_get = uex.get
    failing = {12}

    async def broken(path: str, params: dict[str, int] | None = None) -> Any:
        if path == "/commodities_routes" and (params or {}).get("id_terminal_origin") in failing:
            raise WikiError(f"{path}: HTTP 500")
        return await real_get(path, params)

    monkeypatch.setattr(uex, "get", broken)
    monkeypatch.setattr(Trade, "terminals_at", _arc_l1_and_area18)
    arguments = {"start": "ARC-L1", "commodity": "", "end": "", "cargo_scu": 0}
    error, result = _run(_routes(uex), arguments)
    assert not error
    assert [r["commodity"] for r in result["routes"]] == ["Quantainium"]
    failing.add(1)
    error, result = _run(_routes(uex), arguments)
    assert error
    assert "HTTP 500" in result["error"]


def test_a_start_and_commodity_uex_refuses_ask_the_commoditys_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review of #211: should UEX refuse an origin and a commodity together, the
    commodity's own list still answers."""
    uex = FakeTradeUex()
    real_get = uex.get

    async def refuses_both(path: str, params: dict[str, int] | None = None) -> Any:
        query = params or {}
        if path == "/commodities_routes" and "id_commodity" in query and len(query) > 1:
            return {"status": "invalid_input", "message": "one filter only"}
        return await real_get(path, params)

    monkeypatch.setattr(uex, "get", refuses_both)
    error, result = _run(
        _routes(uex), {"start": "Hurston", "commodity": "laranite", "end": "", "cargo_scu": 0}
    )
    assert not error
    assert [r["sell_at"] for r in result["routes"]] == ["New Babbage", "Area18"]


def test_a_commodity_that_fits_a_few_names_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #158: no test set also_matches, or asked when too many tie."""
    trade = _trade()
    commodity, others = trade.commodity_match("aranite")  # Laranite and Caranite tie
    assert commodity.name in ("Laranite", "Caranite")
    assert others
    import tycho.knowledge.trade

    monkeypatch.setattr(tycho.knowledge.trade, "GUESS_AMONG", 1)
    with pytest.raises(TradeError, match="name one"):
        trade.commodity_match("aranite")


def test_a_guessed_end_is_named() -> None:
    _, result = _run(
        _routes(), {"start": "Hurston", "commodity": "", "end": "Lagrange", "cargo_scu": 0}
    )
    assert result["also_ends"]


def test_a_capped_commodity_list_falls_back_to_the_starts_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#171: UEX stops a commodity's routes at 500, so a start can be missing from it.
    A whole system is asked by the commodity's list, then from a few of its terminals."""
    import tycho.knowledge.trade_lookup

    uex = FakeTradeUex()
    real = uex._routes  # pyright: ignore[reportPrivateUsage]

    def capped(query: dict[str, Any]) -> list[dict[str, Any]]:
        if list(query) == ["id_commodity"]:  # the cap reached, with none from Stanton
            return [r for r in real({"id_terminal_origin": 12}) if r["id_commodity"] == 70]
        return real(query)

    monkeypatch.setattr(uex, "_routes", capped)
    monkeypatch.setattr(tycho.knowledge.trade_lookup, "ROUTES_CAP", 1)
    _, result = _run(
        _routes(uex), {"start": "Stanton", "commodity": "laranite", "end": "", "cargo_scu": 0}
    )
    assert [r["sell_at"] for r in result["routes"]] == ["New Babbage", "Area18"]


def test_a_capped_start_list_falls_back_to_the_commodities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should UEX ever give a start's routes without narrowing them to the commodity,
    a capped list without it still finds the routes, from the commodity's list."""
    import tycho.knowledge.trade_lookup

    uex = FakeTradeUex()
    real = uex._routes  # pyright: ignore[reportPrivateUsage]

    def ignores_the_commodity(query: dict[str, Any]) -> list[dict[str, Any]]:
        if "id_planet_origin" in query:  # capped, and other commodities only
            return [r for r in real({"id_planet_origin": 1}) if r["id_commodity"] != 47]
        return real(query)

    monkeypatch.setattr(uex, "_routes", ignores_the_commodity)
    monkeypatch.setattr(tycho.knowledge.trade_lookup, "ROUTES_CAP", 1)
    _, result = _run(
        _routes(uex), {"start": "Hurston", "commodity": "laranite", "end": "", "cargo_scu": 0}
    )
    assert [r["sell_at"] for r in result["routes"]] == ["New Babbage", "Area18"]


def test_an_uncapped_empty_list_doesnt_ask_again() -> None:
    uex = FakeTradeUex()
    _, result = _run(
        _routes(uex), {"start": "Pyro", "commodity": "laranite", "end": "", "cargo_scu": 0}
    )
    assert result["routes"] == []
    assert [path for path, _ in uex.calls].count("/commodities_routes") == 1


# --- A slow list is still kept (#231) ------------------------------------------------------------


class _SlowRoutes(FakeTradeUex):
    """UEX that takes ``delay`` seconds for a route list."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    async def get(self, path: str, params: Any = None) -> Any:
        if path == "/commodities_routes":
            await asyncio.sleep(self.delay)
        return await super().get(path, params)


def test_a_route_list_slower_than_the_look_up_is_kept_for_next_time() -> None:
    """#231: the look-up's limit cancelled the fetch before the list was kept, so the
    same question failed again every time."""
    uex = _SlowRoutes(delay=0.2)

    async def ask() -> list[dict[str, Any]]:
        trade = Trade(uex)
        await trade.load()
        params = {"id_commodity": 47}
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(trade.routes(params), 0.05)  # the look-up gives up
        await asyncio.sleep(0.3)  # UEX answers anyway
        return await asyncio.wait_for(trade.routes(params), 0.05)

    rows = asyncio.run(ask())
    assert rows
    assert rows == uex._routes({"id_commodity": 47})  # pyright: ignore[reportPrivateUsage]
    assert [path for path, _ in uex.calls].count("/commodities_routes") == 1


def test_asking_again_while_a_list_is_on_its_way_waits_for_the_same_fetch() -> None:
    uex = _SlowRoutes(delay=0.05)

    async def ask() -> None:
        trade = Trade(uex)
        await trade.load()
        await asyncio.gather(*(trade.routes({"id_commodity": 47}) for _ in range(3)))

    asyncio.run(ask())
    assert [path for path, _ in uex.calls].count("/commodities_routes") == 1


def test_a_fetch_that_fails_after_its_look_up_gave_up_is_asked_again() -> None:
    uex = _SlowRoutes(delay=0.1)

    async def ask() -> list[dict[str, Any]]:
        trade = Trade(uex)
        await trade.load()
        uex.offline = True
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(trade.routes({"id_commodity": 47}), 0.02)
        await asyncio.sleep(0.2)  # it fails, unwatched, and nothing is kept
        uex.offline = False
        uex.delay = 0
        return await trade.routes({"id_commodity": 47})

    assert asyncio.run(ask()) == uex._routes({"id_commodity": 47})  # pyright: ignore[reportPrivateUsage]
    assert [path for path, _ in uex.calls].count("/commodities_routes") == 2


def test_closing_stops_the_fetches_still_on_their_way() -> None:
    """#240: a fetch left running when Tycho quit warned "Task was destroyed"."""
    uex = _SlowRoutes(delay=10.0)

    async def ask() -> Trade:
        trade = Trade(uex)
        await trade.load()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(trade.routes({"id_commodity": 47}), 0.02)
        await trade.close()
        await trade.close()  # each look-up sharing it closes it
        return trade

    trade = asyncio.run(ask())
    assert not trade._fetching  # pyright: ignore[reportPrivateUsage]
