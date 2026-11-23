"""Which missions give a blueprint, and which blueprints a mission gives (#596): the
blueprint look-up, against a scripted Wiki API."""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from tycho.games.star_citizen import blueprints
from tycho.games.star_citizen.blueprints import BlueprintLookup
from tycho.games.star_citizen.wiki import WikiError

API = "https://api.star-citizen.wiki/api"


def _mission(
    uuid: str,
    title: str,
    giver: str,
    gives: list[str],
    *,
    system: str = "Nyx",
    standing: tuple[str, int] | None = None,
    chain: bool = False,
    illegal: bool = False,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "title": title,
        "mission_giver": giver,
        "faction": {"name": giver},
        "star_systems": [{"name": f"{system} System"}] if system else [],
        "min_standing": {"name": standing[0], "min_reputation": standing[1]} if standing else None,
        "min_standing_name": standing[0] if standing else None,
        "has_prerequisites": chain,
        "illegal": illegal,
        "game_version": "4.10.3-LIVE.1",
        "link": f"{API}/missions/{uuid}",
        "blueprints": [{"name": n, "link": f"{API}/blueprints/{n}"} for n in gives],
    }


OMNISKY = ["Omnisky III Cannon", "Omnisky VI Cannon", "Omnisky IX Cannon"]
SUITS = ["Neutrino Racing Flight Suit", "Novian Crossbow"]
KILLSHOT = ["Killshot Rifle", 'Killshot "Dominion Camo" Rifle', "Deadrig Shotgun"]

PAGES = [
    [
        _mission(
            "tsg",
            "Tactical Strike Group Needed",
            "InterSec Defense Solutions",
            OMNISKY + SUITS,
            chain=True,
        ),
        # A second variant of the same mission, from the same giver: one entry.
        _mission(
            "tsg2",
            "Tactical Strike Group Needed",
            "InterSec Defense Solutions",
            OMNISKY,
            system="Stanton",
        ),
        _mission(
            "vaughn1",
            "A Challenging Contract",
            "Vaughn",
            KILLSHOT[:2],
            standing=("Jr. Contractor", 1000),
            illegal=True,
        ),
    ],
    [
        # Only this variant gives the Deadrig: its own record holds the pool.
        _mission(
            "vaughn2",
            "A Challenging Contract",
            "Vaughn",
            ["Deadrig Shotgun"],
            standing=("Jr. Contractor", 1000),
        ),
        _mission(
            "bounty",
            "Bounty Assignment: [TargetName] (HRT)",
            "Northrock Service Group",
            ["F55 LMG"],
            standing=("Neutral", 0),
        ),
    ],
]
POOLS: dict[str, list[tuple[float, list[str]]]] = {
    "tsg": [(1.0, OMNISKY), (0.5, SUITS)],
    "tsg2": [(1.0, OMNISKY)],
    "vaughn1": [(1.0, KILLSHOT[:2])],
    "vaughn2": [(0.25, ["Deadrig Shotgun"])],
    "bounty": [(1.0, ["F55 LMG"])],
}


class FakeMissionWiki:
    def __init__(self, *, offline: bool = False, slow_records: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.offline = offline
        self.slow_records = slow_records

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        query = dict(params or {})
        self.calls.append((path, query))
        if self.offline:
            raise WikiError("offline")
        if path == "/api/missions":
            assert query["filter[has_blueprints]"] == 1
            number = int(query["page[number]"])
            return {"data": PAGES[number - 1], "meta": {"last_page": len(PAGES)}}
        uuid = path.rsplit("/", 1)[-1]
        if self.slow_records:
            await asyncio.sleep(self.slow_records)
        pools = [
            {"drop_chance": chance, "items": [{"name": n} for n in names]}
            for chance, names in POOLS[uuid]
        ]
        return {"data": {"uuid": uuid, "blueprints": pools}}

    def page_calls(self) -> int:
        return sum(1 for path, _ in self.calls if path == "/api/missions")


def _run(lookup: BlueprintLookup, blueprint: str = "", mission: str = "") -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        result = await lookup.run({"blueprint": blueprint, "mission": mission})
        assert not result.is_error, result.content
        return json.loads(result.content)

    return asyncio.run(go())


def test_a_blueprint_says_which_missions_give_it_and_its_pool() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), blueprint="omnisky iii")
    assert result["blueprint"] == "Omnisky III Cannon"
    assert result["given_by"] == 1  # two variants, one mission
    (mission,) = result["missions"]
    assert mission["title"] == "Tactical Strike Group Needed"
    assert mission["giver"] == "InterSec Defense Solutions"
    assert mission["systems"] == ["Nyx", "Stanton"]
    assert mission["after_earlier_missions"] is True
    # The pool always rolls, but gives one of three: never "always drops it".
    assert (mission["pool_chance_percent"], mission["pool_size"]) == (100, 3)
    assert result["game_version"] == "4.10.3-LIVE.1"
    assert result["source"].startswith("Star Citizen Wiki API")


def test_the_pool_comes_from_the_variant_that_gives_the_blueprint() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), blueprint="deadrig shotgun")
    (mission,) = result["missions"]
    assert (mission["pool_chance_percent"], mission["pool_size"]) == (25, 1)
    assert mission["standing_needed"] == "Jr. Contractor"
    assert mission["illegal"] is True


def test_the_plain_name_beats_its_variants_which_are_named_too() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), blueprint="killshot rifle")
    assert result["blueprint"] == "Killshot Rifle"
    assert result["also_matches"] == ['Killshot "Dominion Camo" Rifle']


def test_a_blueprint_no_mission_gives_is_a_miss_with_close_names() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), blueprint="P4-AR")
    assert "not_found" in result
    assert "item_lookup" in result["not_found"]
    assert len(result["closest"]) == 3


def test_a_giver_lists_the_blueprints_from_all_its_missions() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), mission="vaughn")
    assert result["giver"] == "Vaughn"
    assert result["missions_with_blueprints"] == 1
    assert result["blueprints"] == KILLSHOT
    assert result["blueprint_count"] == 3
    assert result["lowest_standing_needed"] == "Jr. Contractor"


def test_a_long_list_says_how_many_more_there_are(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live, "Vaughn gives blueprints for 12 items" was read off a list cut to 12 of 41.
    monkeypatch.setattr(blueprints, "SHOWN_BLUEPRINTS", 2)
    result = _run(BlueprintLookup(FakeMissionWiki()), mission="vaughn")
    assert result["blueprints"] == KILLSHOT[:2]
    assert result["more_not_listed"] == 1
    assert result["blueprint_count"] == 3


def test_a_mission_title_lists_its_pools() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), mission="tactical strike group")
    assert result["mission"] == "Tactical Strike Group Needed"
    (giver,) = result["from"]
    assert giver["giver"] == "InterSec Defense Solutions"
    assert giver["pools"] == [
        {"pool_chance_percent": 100, "pool_size": 3, "blueprints": OMNISKY},
        {"pool_chance_percent": 50, "pool_size": 2, "blueprints": SUITS},
    ]


def test_a_title_matches_without_its_placeholders_and_neutral_needs_no_standing() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), mission="bounty assignment")
    assert result["mission"] == "Bounty Assignment: [TargetName] (HRT)"
    (giver,) = result["from"]
    assert "standing_needed" not in giver


def test_an_unknown_mission_is_a_miss() -> None:
    result = _run(BlueprintLookup(FakeMissionWiki()), mission="xenothreat invasion")
    assert "not_found" in result


def test_nothing_asked_says_to_ask() -> None:
    assert "not_found" in _run(BlueprintLookup(FakeMissionWiki()))


def test_the_missions_load_once_in_pages_and_are_kept() -> None:
    wiki = FakeMissionWiki()
    lookup = BlueprintLookup(wiki)

    async def go() -> None:
        await lookup.prepare()
        await lookup.run({"blueprint": "omnisky", "mission": ""})
        await lookup.run({"blueprint": "", "mission": "vaughn"})

    asyncio.run(go())
    assert wiki.page_calls() == len(PAGES)


def test_they_load_again_after_hours() -> None:
    wiki = FakeMissionWiki()
    now = [0.0]
    lookup = BlueprintLookup(wiki, clock=lambda: now[0])
    _run(lookup, mission="vaughn")
    now[0] = blueprints.KEEP_SECONDS + 1
    _run(lookup, mission="vaughn")
    assert wiki.page_calls() == 2 * len(PAGES)


def test_a_failed_start_is_quiet_and_the_look_up_says_the_source_is_down() -> None:
    wiki = FakeMissionWiki(offline=True)
    lookup = BlueprintLookup(wiki)

    async def go() -> bool:
        await lookup.prepare()  # never raises
        return (await lookup.run({"blueprint": "omnisky", "mission": ""})).is_error

    assert asyncio.run(go())
    wiki.offline = False
    assert _run(lookup, blueprint="omnisky iii")["blueprint"] == "Omnisky III Cannon"


def test_slow_pools_are_left_out_rather_than_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blueprints, "POOL_SECONDS", 0.05)
    result = _run(BlueprintLookup(FakeMissionWiki(slow_records=1.0)), blueprint="omnisky iii")
    (mission,) = result["missions"]
    assert mission["title"] == "Tactical Strike Group Needed"
    assert "pool_size" not in mission
