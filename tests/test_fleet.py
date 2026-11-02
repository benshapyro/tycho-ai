"""The player's fleet (ADR 15): kept in a file, changed by voice through update_fleet,
and described in each request's header."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_knowledge import CONCEPTS, LIBRARY
from tycho.brain.claude import history
from tycho.knowledge.fleet import Fleet, FleetUpdate
from tycho.knowledge.ships import ShipLibrary, concepts_from_uex

WITH_CONCEPTS = LIBRARY.with_concepts(concepts_from_uex(CONCEPTS[:3], LIBRARY.ships))


def _update(
    fleet: Fleet, library: ShipLibrary | None = WITH_CONCEPTS, **arguments: Any
) -> tuple[bool, Any]:
    tool = FleetUpdate(fleet, lambda: library)
    full: dict[str, object] = {"add": [], "remove": [], "flying": "", **arguments}
    result = asyncio.run(tool.run(full))
    return result.is_error, json.loads(result.content)


# --- The file ----------------------------------------------------------------------------------


def test_no_file_is_an_empty_fleet(tmp_path: Path) -> None:
    fleet = Fleet.load(tmp_path / "fleet.toml")
    assert fleet.ships == []
    assert fleet.describe(LIBRARY) == ""


def test_the_fleet_round_trips_through_its_file(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    Fleet(path, ["Caterpillar", 'A "quoted" name']).save()
    assert Fleet.load(path).ships == ["Caterpillar", 'A "quoted" name']
    assert path.read_text("utf-8").startswith("# Your ships, for Tycho.")
    assert not list(tmp_path.glob("*.partial"))


def test_a_broken_file_starts_empty_rather_than_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text("ships = [ not toml", "utf-8")
    assert Fleet.load(path).ships == []
    assert "Couldn't read your fleet" in caplog.text


# --- What each request is told ------------------------------------------------------------------


def test_the_header_lists_the_ships_with_cargo_and_role(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "f.toml", ["Caterpillar", "Kraken"], flying="Caterpillar")
    line = fleet.describe(WITH_CONCEPTS)
    assert line == (
        "[The player's ships: Caterpillar (Multi-Role, Medium Freight, 576 SCU); "
        "Kraken (carrier, docking, military, 3792 SCU, concept, not flyable yet). "
        "Flying now: Caterpillar (Multi-Role, Medium Freight, 576 SCU)]"
    )
    assert Fleet(tmp_path / "f.toml", ["Mystery Ship"]).describe(None) == (
        "[The player's ships: Mystery Ship]"
    )


def test_the_header_goes_after_the_date_outside_the_cached_briefing() -> None:
    import datetime

    (message,) = history([], "what should I haul", datetime.date(2026, 11, 2), "[ships]")
    assert message["content"][0] == {"type": "text", "text": "[2 November 2026]\n[ships]"}  # type: ignore[index]
    (plain,) = history([], "hi", datetime.date(2026, 11, 2))
    assert plain["content"][0] == {"type": "text", "text": "[2 November 2026]"}  # type: ignore[index]


# --- Changing it by voice -----------------------------------------------------------------------


def test_adding_uses_the_games_names_and_saves(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml")
    error, result = _update(fleet, add=["the Cat", "cutty black"])
    assert not error
    assert result["added"] == ["Caterpillar", "Cutlass Black"]
    assert Fleet.load(fleet.path).ships == ["Caterpillar", "Cutlass Black"]


def test_a_ship_already_owned_isnt_added_twice_however_its_written(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml", ["the Cat"])  # as a player might write it by hand
    _, result = _update(fleet, add=["Caterpillar"])
    assert "added" not in result
    assert fleet.ships == ["the Cat"]


def test_removing_finds_the_ship_however_its_written(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml", ["Cat", "C1 Spirit"])
    _, result = _update(fleet, remove=["Caterpillar"])
    assert result["removed"] == ["Cat"]
    assert Fleet.load(fleet.path).ships == ["C1 Spirit"]
    _, missing = _update(fleet, remove=["400i"])
    assert missing["problems"] == [{"asked": "400i", "note": "400i isn't in the fleet"}]


def test_flying_lasts_for_the_session_and_isnt_saved(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml", ["Caterpillar"])
    _, result = _update(fleet, flying="msr")
    assert result["flying"] == "Mercury Star Runner"
    assert fleet.flying == "Mercury Star Runner"
    assert fleet.ships == ["Caterpillar"]  # flying a ship doesn't mean owning it
    assert not fleet.path.exists()  # nothing to save
    _update(fleet, flying="none")
    assert fleet.flying == ""


def test_an_unclear_name_changes_nothing_and_says_why(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml")
    _, result = _update(fleet, add=["Connie", "Galaxy Explorer 9000"])
    assert fleet.ships == []
    unclear, unknown = result["problems"]
    assert unclear["note"] == "ask which"
    assert len(unclear["could_be"]) == 3
    assert unknown["note"] == "not found"


def test_concept_ships_can_be_owned(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml")
    _, result = _update(fleet, add=["Kraken"])
    assert result["added"] == ["Kraken"]


def test_before_the_ship_data_loads_it_says_so(tmp_path: Path) -> None:
    error, result = _update(Fleet(tmp_path / "fleet.toml"), library=None, add=["Caterpillar"])
    assert error
    assert "still loading" in result["error"]


def test_the_tool_schema_suits_strict_tool_use(tmp_path: Path) -> None:
    schema = FleetUpdate(Fleet(tmp_path / "f.toml"), lambda: None).input_schema
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["add", "remove", "flying"]
