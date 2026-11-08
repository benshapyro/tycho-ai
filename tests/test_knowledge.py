"""Knowledge (ADR 15): the ship data, finding ships by name, the local copy and the look-up."""

import asyncio
import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.fake_wiki import FakeUex, FakeWiki, uex_vehicle, vehicle
from tycho.knowledge import Ambiguous, NotFound, Ship, ShipLibrary, ShipLookup, ship_lookup, store
from tycho.knowledge.ships import (
    concept_from_uex,
    concepts_from_uex,
    rescued,
    searched,
    ship_from_wiki,
    ships_from_wiki,
)
from tycho.knowledge.store import ShipDataError, ensure, file_for, load, save
from tycho.knowledge.wiki import Hit

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
CONCEPTS = [
    uex_vehicle(
        "Kraken",
        scu=3792,
        crew="10",
        length=270,
        width=104,
        height=64,
        pad_type="XL",
        is_military=1,
        is_carrier=1,
        is_docking=1,
    ),
    uex_vehicle("Galaxy", "Roberts Space Industries", scu=64, crew="1,6"),
    uex_vehicle("Merchantman", "Banu Souli", scu=2880, crew="4,8"),
    uex_vehicle("Galaxy Cargo Module", "Roberts Space Industries", is_addon=1),
    uex_vehicle("Caterpillar", scu=999),  # in the game files already: theirs wins
    uex_vehicle("Cutlass Black", is_concept=0),  # not a concept
]
LIBRARY = ShipLibrary(ships_from_wiki(FLEET), version="4.10.2-LIVE.1", as_of="2026-11-01")


def _name(found: object) -> str:
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


def test_a_name_asked_again_is_remembered_with_the_new_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every request's fleet line finds each owned ship again (audit, 2026-11-03).
    library = ShipLibrary(LIBRARY.ships, version=LIBRARY.version, as_of=LIBRARY.as_of)
    first = library.find("Kraken")
    assert isinstance(first, NotFound)
    searches: list[str] = []
    real = library._search  # pyright: ignore[reportPrivateUsage]

    def search(asked: str, query: tuple[str, ...]) -> Ship | Ambiguous | NotFound:
        searches.append(asked)
        return real(asked, query)

    monkeypatch.setattr(library, "_search", search)
    again = library.find("the kraken")  # the same words once "the" is dropped
    assert searches == []
    assert isinstance(again, NotFound)
    assert again.closest == first.closest
    assert again.asked == "the kraken"
    assert library.find("Caterpillar") is library.find("caterpillar")
    assert searches == ["Caterpillar"]


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
    assert wiki.calls.count("/api/vehicles") == 6  # 12 ships, 2 a page
    assert file_for(tmp_path, "4.10.2-LIVE.1").exists()

    wiki.calls.clear()
    again = asyncio.run(ensure(wiki, tmp_path, _today))
    assert wiki.calls == ["/api/game-versions"]  # the copy on disk is used
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
    assert "/api/vehicles" in wiki.calls


def test_data_that_cant_be_saved_is_used_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A full disk or a locked file left look-ups "still loading" all session (audit, 2026-11-03).
    def full(*_: Any, **__: Any) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(store, "save", full)
    library = asyncio.run(ensure(FakeWiki(FLEET), tmp_path, _today))
    assert len(library) == len(FLEET)
    assert "couldn't save it" in caplog.text


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


def test_an_unexpected_failure_says_why_instead_of_still_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: Any, **__: Any) -> ShipLibrary:
        raise PermissionError("the file is in use")

    monkeypatch.setattr(ship_lookup, "ensure", broken)
    lookup = ShipLookup(FakeWiki(FLEET), tmp_path)
    asyncio.run(lookup.prepare())  # never raises
    error, content = _run(lookup, {"names": ["Caterpillar"]})
    assert error
    assert "still loading" not in content["error"]
    assert "the file is in use" in content["error"]


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


# --- A miss, read again with the Wiki API's search (#153) ---------------------------------------


def test_an_item_asked_of_the_ship_look_up_says_its_an_item() -> None:
    """ADR 15's eval: "what's the DPS on the gallant" once got "no ship by that name"."""
    wiki = FakeWiki(FLEET)
    error, content = _run(ShipLookup(wiki, Path(), library=LIBRARY), {"names": ["the Gallant"]})
    assert not error
    assert content["ships"] == [
        {
            "asked": "the Gallant",
            "is_an_item": "Gallant Rifle",
            "note": "an item, not a ship: use item_lookup with this name",
        }
    ]
    assert wiki.search.asked == ["gallant"]


def test_a_ship_found_is_never_searched_for() -> None:
    wiki = FakeWiki(FLEET)
    _run(ShipLookup(wiki, Path(), library=LIBRARY), {"names": ["the Cat", "Connie"]})
    assert wiki.search.asked == []


STARFIGHTERS = [
    vehicle("Ares Star Fighter Ion", "Crusader Industries", class_name="CRUS_Starfighter_Ion"),
    vehicle(
        "Ares Star Fighter Inferno", "Crusader Industries", class_name="CRUS_Starfighter_Inferno"
    ),
]


def test_a_miss_the_search_finds_one_fitting_ship_for_is_that_ship() -> None:
    # The search reads class names too, so it can find a ship the names alone miss.
    library = ShipLibrary(
        ships_from_wiki(STARFIGHTERS), version="4.10.2-LIVE.1", as_of="2026-11-01"
    )
    missed = library.find("Starfighter Ion")
    assert isinstance(missed, NotFound)
    hits = [Hit("vehicles", "Crusader Ares Star Fighter Ion", "Combat")]
    assert _name(rescued(library, missed, hits)) == "Ares Star Fighter Ion"


def test_the_ship_the_search_names_must_fit_the_words_as_said() -> None:
    """#221: the search is asked "mantis" for "Mantis gatling", and names the RSI
    Mantis, the ship the gun is named for. Asked of the ship look-up, it's the gun."""
    fleet = [*FLEET, vehicle("Mantis", class_name="RSI_Mantis")]
    library = ShipLibrary(ships_from_wiki(fleet), version="4.10.2-LIVE.1", as_of="2026-11-01")
    wiki = FakeWiki(fleet)
    error, content = _run(ShipLookup(wiki, Path(), library=library), {"names": ["Mantis gatling"]})
    assert not error
    assert wiki.search.asked == ["mantis"]
    assert content["ships"][0]["is_an_item"] == "Mantis GT-220 Gatling"


def test_a_ship_the_search_names_that_doesnt_fit_is_only_a_closest_name() -> None:
    hits = [Hit("vehicles", "Drake Caterpillar", "Industrial")]
    found = rescued(LIBRARY, NotFound("Caterpillar Mk II", ()), hits)
    assert isinstance(found, NotFound)
    assert found.closest == ("Caterpillar",)


def test_a_ship_miss_asks_the_search_once_in_a_while() -> None:
    """#221: the search allows 60 requests a minute, and the same miss misses again."""
    now = [0.0]
    wiki = FakeWiki(FLEET)
    lookup = ShipLookup(wiki, Path(), library=LIBRARY, clock=lambda: now[0])
    for _ in range(2):
        error, content = _run(lookup, {"names": ["the Odin"]})
        assert not error
        assert "closest" in content["ships"][0]
    assert wiki.search.asked == ["odin"]
    now[0] += ship_lookup.FRESH_SECONDS
    _run(lookup, {"names": ["the Odin"]})
    assert wiki.search.asked == ["odin", "odin"]


def test_a_failed_search_is_asked_again_next_time() -> None:
    wiki = FakeWiki(FLEET)
    lookup = ShipLookup(wiki, Path(), library=LIBRARY)
    wiki.search.down = True
    _run(lookup, {"names": ["the Gallant"]})
    wiki.search.down = False
    _, content = _run(lookup, {"names": ["the Gallant"]})
    assert content["ships"][0]["is_an_item"] == "Gallant Rifle"
    assert wiki.search.asked == ["gallant", "gallant"]


def test_a_miss_offers_the_closest_across_ships_and_items() -> None:
    hits = [Hit("items", "Gallant Rifle", "FPS Weapon"), Hit("items", "Galant Paint", "Paints")]
    found = rescued(LIBRARY, NotFound("Gallant Bomber", ("400i", "C1 Spirit")), hits)
    assert isinstance(found, NotFound)
    assert found.closest[0] == "Gallant Rifle"  # liveries are never items
    assert "Galant Paint" not in found.closest


def test_a_slow_search_leaves_the_miss_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ship_lookup, "SEARCH_SECONDS", 0.05)
    wiki = FakeWiki(FLEET)
    wiki.search.delay = 1.0
    error, content = _run(ShipLookup(wiki, Path(), library=LIBRARY), {"names": ["the Gallant"]})
    assert not error
    assert content["ships"][0]["asked"] == "the Gallant"
    assert "is_an_item" not in content["ships"][0]
    assert "closest" in content["ships"][0]


def test_a_failed_search_leaves_the_miss_as_it_was() -> None:
    wiki = FakeWiki(FLEET)
    wiki.search.down = True
    error, content = _run(
        ShipLookup(wiki, Path(), library=LIBRARY), {"names": ["the Gallant", "the Cat"]}
    )
    assert not error
    gallant, cat = content["ships"]
    assert "closest" in gallant
    assert cat["cargo_scu"] == 576


def test_a_ships_search_says_what_players_mean() -> None:
    assert searched("the cutty black") == "cutlass black"
    assert searched("FS-9") == "fs-9"


# --- Ships still in concept --------------------------------------------------------------------


def test_a_concept_ship_says_it_isnt_in_the_game() -> None:
    kraken = concept_from_uex(CONCEPTS[0])
    assert kraken.is_concept
    assert kraken.name == "Kraken"
    assert dict(kraken.facts) == {
        "name": "Drake Kraken",
        "maker": "Drake Interplanetary",
        "kind": "spaceship",
        "status": "concept: announced and sold, not in the game yet; details may change",
        "roles": "carrier, docking, military",
        "crew": "10",
        "cargo_scu": 3792,
        "length_m": 270,
        "beam_m": 104,
        "height_m": 64,
        "landing_pad": "XL",
    }
    assert concept_from_uex(CONCEPTS[1]).facts["crew"] == "1-6"


def test_concepts_leave_out_modules_and_ships_already_in_the_game() -> None:
    from tycho.knowledge.uex import concept_vehicles

    records = asyncio.run(concept_vehicles(FakeUex(CONCEPTS)))
    names = [s.name for s in concepts_from_uex(records, LIBRARY.ships)]
    assert names == ["Kraken", "Galaxy", "Merchantman"]


def test_concepts_are_found_by_name_and_game_ships_still_win() -> None:
    library = LIBRARY.with_concepts(concepts_from_uex(CONCEPTS[:3], LIBRARY.ships))
    assert library.concepts
    assert _name(library.find("the Kraken")) == "Kraken"
    assert _name(library.find("drake kraken")) == "Kraken"
    assert library.find("Caterpillar") == LIBRARY.find("Caterpillar")
    # A ship only just revealed isn't the concept it sounds like.
    baby = library.find("baby Merchantman")
    assert isinstance(baby, NotFound)
    assert _name(library.find("Banu Merchantman")) == "Merchantman"
    assert len(library.with_concepts([])) == len(LIBRARY)  # replaced, never doubled


def test_the_local_copy_keeps_the_concepts(tmp_path: Path) -> None:
    library = asyncio.run(ensure(FakeWiki(FLEET), tmp_path, _today, concepts=FakeUex(CONCEPTS)))
    assert library.concepts
    assert len(library) == len(FLEET) + 3
    copy = load(file_for(tmp_path, "4.10.2-LIVE.1"))
    assert copy.concepts
    assert _name(copy.find("Galaxy")) == "Galaxy"


def test_without_uex_the_concepts_are_fetched_next_time_alone(tmp_path: Path) -> None:
    wiki, uex = FakeWiki(FLEET), FakeUex(CONCEPTS)
    uex.offline = True
    first = asyncio.run(ensure(wiki, tmp_path, _today, concepts=uex))
    assert not first.concepts
    assert len(first) == len(FLEET)

    uex.offline = False
    wiki.calls.clear()
    again = asyncio.run(ensure(wiki, tmp_path, _today, concepts=uex))
    assert wiki.calls == ["/api/game-versions"]  # the game ships aren't downloaded again
    assert again.concepts
    assert load(file_for(tmp_path, "4.10.2-LIVE.1")).concepts

    uex.calls.clear()
    asyncio.run(ensure(wiki, tmp_path, _today, concepts=uex))
    assert uex.calls == []  # once they're in, UEX isn't asked again this patch


def test_the_look_up_answers_for_a_concept_ship(tmp_path: Path) -> None:
    lookup = ShipLookup(FakeWiki(FLEET), tmp_path, concepts=FakeUex(CONCEPTS))
    asyncio.run(lookup.prepare())
    _, content = _run(lookup, {"names": ["Kraken"]})
    assert "UEX" in content["source"]
    (kraken,) = content["ships"]
    assert kraken["status"].startswith("concept")
    assert kraken["cargo_scu"] == 3792
