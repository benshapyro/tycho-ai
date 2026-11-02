"""Knowledge (ADR 15): the ship data, finding ships by name, the local copy and the look-up."""

import asyncio
import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.fake_wiki import FakeWiki, vehicle
from tycho.knowledge import Ambiguous, NotFound, Ship, ShipLibrary, ShipLookup
from tycho.knowledge.ships import ship_from_wiki, ships_from_wiki
from tycho.knowledge.store import ShipDataError, ensure, file_for, load, save

FLEET = [
    vehicle("Constellation Mk IV Andromeda", game_name="RSI Constellation Mk IV Andromeda"),
    vehicle("Constellation Mk IV Aquila"),
    vehicle("Constellation Mk IV Taurus"),
    vehicle("Caterpillar", "Drake Interplanetary", cargo_capacity=576),
    vehicle("Cutlass Black", "Drake Interplanetary"),
    vehicle("Cutlass Black PYAM Exec", "Drake Interplanetary"),
    vehicle("C1 Spirit", "Crusader Industries"),
    vehicle("C2 Hercules Starlifter", "Crusader Industries"),
    vehicle("Mercury Star Runner", "Crusader Industries"),
    vehicle("400i", "Origin Jumpworks"),
    vehicle("F7A Hornet Mk II", "Anvil Aerospace"),
    vehicle("600i Explorer", "Origin Jumpworks"),
]
LIBRARY = ShipLibrary(ships_from_wiki(FLEET), version="4.10.2-LIVE.1", as_of="2026-11-01")


def _name(found: Ship | Ambiguous | NotFound) -> str:
    assert isinstance(found, Ship), found
    return found.name


# --- The facts kept --------------------------------------------------------------------------


def test_a_record_becomes_compact_spoken_facts() -> None:
    record = vehicle(
        "Caterpillar",
        "Drake Interplanetary",
        cargo_capacity=576,
        crew={"min": 4, "max": 4},
        quantum={"quantum_range": 153644982020},
        mass_total=1679725,
        insurance={"claim_time": 18.63, "expedite_time": 6.21, "expedite_cost": 7730},
        loaner=[{"name": "Buccaneer"}],
        uex_prices={
            "purchase": [
                {"terminal_name": "Teasa, Lorville", "price_buy": 11850300},
                {"terminal_name": "Astro Armada, Area18", "price_buy": 11700000},
                {"terminal_name": "Nowhere", "price_buy": None},
            ],
            "rental": [],
        },
        production_status={"en_EN": "flight-ready"},
        weaponry=None,
    )
    facts = ship_from_wiki(record).facts
    assert facts["name"] == "Drake Caterpillar"
    assert facts["maker"] == "Drake Interplanetary"
    assert facts["crew"] == "4"
    assert facts["cargo_scu"] == 576
    assert facts["size"] == "Large"  # English only
    assert facts["quantum_range_gm"] == 153.6
    assert facts["mass_t"] == 1680
    assert facts["claim_minutes"] == 18.63
    assert facts["loaners"] == ["Buccaneer"]
    assert facts["buy_in_game"] == [  # cheapest first; rows without a price dropped
        {"where": "Astro Armada, Area18", "auec": 11700000},
        {"where": "Teasa, Lorville", "auec": 11850300},
    ]
    assert "rent_in_game" not in facts  # empty facts are left out
    assert "pilot_dps" not in facts
    assert len(json.dumps(dict(facts))) < 1600  # about 400 tokens at most (ADR 15)


def test_ground_vehicles_and_crew_ranges() -> None:
    facts = ship_from_wiki(vehicle("Cyclone", is_spaceship=False, crew={"min": 1, "max": 2})).facts
    assert facts["kind"] == "ground vehicle"
    assert facts["crew"] == "1-2"


def test_one_ship_per_name_preferring_the_one_you_can_buy() -> None:
    display = vehicle("Andromeda", class_name="RSI_Andromeda_GS_Display", health=1)
    event = vehicle("Andromeda", class_name="RSI_Andromeda_BIS2950", health=2)
    real = vehicle(
        "Andromeda",
        class_name="RSI_Andromeda",
        health=3,
        uex_prices={"purchase": [{"terminal_name": "New Deal", "price_buy": 9}]},
    )
    (ship,) = ships_from_wiki([display, event, real])
    assert ship.key == "RSI_Andromeda"
    (plain,) = ships_from_wiki([display, event])
    assert plain.key == "RSI_Andromeda_BIS2950"  # not a display model


# --- Finding a ship by what the player called it ----------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("Andromeda", "Constellation Mk IV Andromeda"),
        ("connie andromeda", "Constellation Mk IV Andromeda"),
        ("the Cat", "Caterpillar"),
        ("Cutlass Black", "Cutlass Black"),  # not the PYAM Exec edition
        ("cutty black", "Cutlass Black"),
        ("MSR", "Mercury Star Runner"),
        ("Andromda", "Constellation Mk IV Andromeda"),  # misspelled
        ("RSI Constellation Andromeda", "Constellation Mk IV Andromeda"),  # with the maker
        ("400i", "400i"),
        ("C1", "C1 Spirit"),
        ("hornet mark 2", "F7A Hornet Mk II"),
    ],
)
def test_finds_ships_by_their_players_names(said: str, expected: str) -> None:
    assert _name(LIBRARY.find(said)) == expected


def test_a_name_that_fits_several_ships_lists_them() -> None:
    found = LIBRARY.find("Connie")
    assert isinstance(found, Ambiguous)
    assert len(found.candidates) == 3
    assert found.more == 0
    assert all(c.startswith("Constellation") for c in found.candidates)


def test_short_codes_are_never_fuzzy() -> None:
    assert _name(LIBRARY.find("C2")) == "C2 Hercules Starlifter"
    assert isinstance(LIBRARY.find("C3"), NotFound)
    assert isinstance(LIBRARY.find("500i"), NotFound)


def test_an_unknown_name_offers_the_closest() -> None:
    found = LIBRARY.find("Kraken")
    assert isinstance(found, NotFound)
    assert len(found.closest) == 3
    assert isinstance(LIBRARY.find("the"), NotFound)


def test_the_briefing_table_has_a_line_per_ship() -> None:
    lines = LIBRARY.table().splitlines()
    assert len(lines) == len(LIBRARY) + 1
    assert "Caterpillar | Drake Interplanetary | Multi-Role, Medium Freight | 1-4 | 576 | " in (
        LIBRARY.table()
    )


# --- The local copy ----------------------------------------------------------------------------


def _today() -> datetime.date:
    return datetime.date(2026, 11, 1)


def test_the_local_copy_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "ships.sqlite"
    save(path, LIBRARY.ships, LIBRARY.version, LIBRARY.as_of)
    copy = load(path)
    assert copy.version == "4.10.2-LIVE.1"
    assert copy.patch == "4.10.2"
    assert copy.as_of == "2026-11-01"
    assert not copy.stale
    assert {s.name: s.facts for s in copy.ships} == {s.name: s.facts for s in LIBRARY.ships}
    assert _name(copy.find("the Cat")) == "Caterpillar"
    assert not list(tmp_path.glob("*.partial"))


def test_it_downloads_once_per_patch(tmp_path: Path) -> None:
    wiki = FakeWiki(FLEET)
    first = asyncio.run(ensure(wiki, tmp_path, _today))
    assert len(first) == len(FLEET)
    assert wiki.calls.count("/api/v2/vehicles") == 6  # 12 ships, 2 a page
    assert file_for(tmp_path, "4.10.2-LIVE.1").exists()

    wiki.calls.clear()
    again = asyncio.run(ensure(wiki, tmp_path, _today))
    assert wiki.calls == ["/api/v2/game-versions"]  # the copy on disk is used
    assert len(again) == len(FLEET)


def test_a_new_patch_downloads_again_and_drops_the_old_copy(tmp_path: Path) -> None:
    wiki = FakeWiki(FLEET)
    asyncio.run(ensure(wiki, tmp_path, _today))
    wiki.version = "4.10.3-LIVE.2"
    library = asyncio.run(ensure(wiki, tmp_path, _today))
    assert library.patch == "4.10.3"
    assert [p.name for p in tmp_path.iterdir()] == ["ships-4.10.3-LIVE.2.sqlite"]


def test_offline_uses_the_newest_copy_marked_stale(tmp_path: Path) -> None:
    wiki = FakeWiki(FLEET)
    asyncio.run(ensure(wiki, tmp_path, _today))
    wiki.offline = True
    library = asyncio.run(ensure(wiki, tmp_path, _today))
    assert library.stale
    assert library.patch == "4.10.2"


def test_offline_with_no_copy_is_a_clear_error(tmp_path: Path) -> None:
    wiki = FakeWiki(FLEET)
    wiki.offline = True
    with pytest.raises(ShipDataError, match="no ship data on this PC"):
        asyncio.run(ensure(wiki, tmp_path, _today))


def test_a_broken_copy_is_downloaded_again(tmp_path: Path) -> None:
    file_for(tmp_path, "4.10.2-LIVE.1").write_bytes(b"not a database")
    library = asyncio.run(ensure(FakeWiki(FLEET), tmp_path, _today))
    assert len(library) == len(FLEET)


def test_an_old_schema_is_downloaded_again(tmp_path: Path) -> None:
    path = file_for(tmp_path, "4.10.2-LIVE.1")
    save(path, LIBRARY.ships, LIBRARY.version, LIBRARY.as_of)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value = '0' WHERE key = 'schema'")
    db.close()
    wiki = FakeWiki(FLEET)
    asyncio.run(ensure(wiki, tmp_path, _today))
    assert "/api/v2/vehicles" in wiki.calls


# --- The look-up -------------------------------------------------------------------------------


def _run(lookup: ShipLookup, arguments: dict[str, Any]) -> Any:
    result = asyncio.run(lookup.run(arguments))
    return result.is_error, json.loads(result.content)


def test_before_the_data_loads_it_says_so(tmp_path: Path) -> None:
    error, content = _run(ShipLookup(FakeWiki(FLEET), tmp_path), {"names": ["Caterpillar"]})
    assert error
    assert "still loading" in content["error"]


def test_it_answers_with_the_source_and_patch(tmp_path: Path) -> None:
    lookup = ShipLookup(FakeWiki(FLEET), tmp_path)
    asyncio.run(lookup.prepare())
    error, content = _run(lookup, {"names": ["the Cat", "Connie", "Kraken", "x", "y"]})
    assert not error
    assert content["source"].startswith("Star Citizen Wiki API")
    assert content["game_version"] == "4.10.2"
    assert content["as_of"] == datetime.date.today().isoformat()
    assert content["stale"] is False
    cat, connie, kraken = content["ships"]  # at most three names
    assert cat["cargo_scu"] == 576
    assert len(connie["matches"]) == 3
    assert "closest" in kraken


def test_if_the_data_cant_be_had_it_says_why(tmp_path: Path) -> None:
    wiki = FakeWiki(FLEET)
    wiki.offline = True
    lookup = ShipLookup(wiki, tmp_path)
    asyncio.run(lookup.prepare())  # never raises
    error, content = _run(lookup, {"names": ["Caterpillar"]})
    assert error
    assert "no ship data on this PC" in content["error"]


@pytest.mark.parametrize("arguments", [{}, {"names": []}, {"names": "Caterpillar"}])
def test_bad_arguments_get_an_error_that_says_what_to_pass(arguments: dict[str, Any]) -> None:
    lookup = ShipLookup(FakeWiki(FLEET), Path(), library=LIBRARY)
    error, content = _run(lookup, arguments)
    assert error
    assert "names" in content["error"]


def test_the_tool_schema_suits_strict_tool_use() -> None:
    schema = ShipLookup(FakeWiki(FLEET), Path(), library=LIBRARY).input_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["names"]
