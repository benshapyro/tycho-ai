"""Trade (ADR 15): commodity and place names, prices and routes from UEX, and the
two look-ups the brain calls."""

import asyncio
import json
from typing import Any

import pytest

from tests.fakes.fake_uex import NOW, FakeTradeUex
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


def test_a_place_that_fits_several_asks_which() -> None:
    with pytest.raises(TradeError, match="could be"):
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
    prices, routes = trade_lookups(uex)

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
    prices, _ = trade_lookups(FakeTradeUex())
    assert isinstance(prices, CommodityPrices)
    return prices


def _routes() -> TradeRoutes:
    _, routes = trade_lookups(FakeTradeUex())
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
    _, routes = trade_lookups(uex)
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


def test_a_name_that_misses_is_an_error_that_says_what_to_try() -> None:
    error, result = _run(_prices(), {"commodity": "unobtainium", "place": ""})
    assert error
    assert "closest" in result["error"]
    error, result = _run(
        _routes(), {"start": "nowhere", "commodity": "", "end": "", "cargo_scu": 0}
    )
    assert error
    assert "no place called" in result["error"]


def test_without_uex_it_says_so_and_tries_again_next_time() -> None:
    uex = FakeTradeUex()
    uex.offline = True
    prices, _ = trade_lookups(uex)
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
