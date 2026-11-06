"""Where to refine an ore, and where to buy a ship (#152): UEX, through the trade data."""

import asyncio
import json
from typing import Any

from tests.fakes.fake_uex import FakeTradeUex
from tycho.knowledge.refining import RefiningLookup, ShipPurchases
from tycho.knowledge.trade_lookup import trade_lookups


def _tools() -> tuple[RefiningLookup, ShipPurchases]:
    _, _, refining, ships = trade_lookups(FakeTradeUex())
    assert isinstance(refining, RefiningLookup)
    assert isinstance(ships, ShipPurchases)
    return refining, ships


def _run(lookup: Any, **arguments: str) -> dict[str, Any]:
    result = asyncio.run(lookup.run(arguments))
    assert not result.is_error
    return json.loads(result.content)


def test_an_ore_is_refined_best_where_its_yield_is_highest() -> None:
    refining, _ = _tools()
    result = _run(refining, ore="laranite", place="")
    assert result["ore"] == "Laranite (Raw)"  # refineries take ore, however it's said
    assert [(r["where"], r["yield_percent"]) for r in result["refineries"]] == [
        ("ARC-L1 Wide Forest Station", 6),
        ("Area18", 3),
    ]
    assert result["methods"][0] == {"name": "Dinyx Solventation", "yield": 3, "cost": 1, "speed": 1}


def test_refining_keeps_to_a_place() -> None:
    refining, _ = _tools()
    result = _run(refining, ore="raw laranite", place="Area 18")
    assert [r["where"] for r in result["refineries"]] == ["Area18"]


def test_an_ore_no_refinery_lists_says_so() -> None:
    refining, _ = _tools()
    result = _run(refining, ore="medical supplies", place="")
    assert "no refinery yields" in result["note"]


def test_an_unknown_ore_is_an_answer() -> None:
    refining, _ = _tools()
    assert "closest" in _run(refining, ore="unobtainium", place="")["not_found"]


def test_a_ship_is_bought_cheapest_first() -> None:
    _, ships = _tools()
    result = _run(ships, ship="cutlass black")
    assert result["ship"] == "Cutlass Black"
    assert [(s["where"], s["auec"]) for s in result["shops"]] == [
        ("Astro Armada", 2_010_960),  # not a commodity terminal: UEX's own name
        ("Area18", 2_100_000),
    ]


def test_a_ship_not_sold_in_game_names_the_closest() -> None:
    _, ships = _tools()
    result = _run(ships, ship="Idris")
    assert result["not_found"].startswith("no ship called 'Idris' is sold in game; closest: ")
    assert "Prospector" in result["not_found"] or "Cutlass" in result["not_found"]


def test_a_ship_said_with_its_maker_or_filler_is_found() -> None:
    """Review of #173: UEX's names have no maker, so "MISC Prospector" missed."""
    _, ships = _tools()
    for said in ("MISC Prospector", "the prospector", "a prospecter"):
        assert _run(ships, ship=said)["ship"] == "Prospector", said
    assert _run(ships, ship="Drake Cutlass Black")["ship"] == "Cutlass Black"


def test_a_ship_name_that_fits_several_variants_names_the_others() -> None:
    _, ships = _tools()
    result = _run(ships, ship="cutlass")
    assert result["ship"] == "Cutlass Black"  # the first, not the last alphabetically
    assert result["also_matches"] == ["Cutlass Steel"]


def test_shops_without_a_price_are_left_out() -> None:
    _, ships = _tools()
    result = _run(ships, ship="Cutlass Steel")
    assert result["shops"] == []
    assert "no in-game price" in result["note"]
