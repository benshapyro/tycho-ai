"""Where to mine a resource (#151): the mining look-up, against a scripted Wiki API."""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from tycho.knowledge.mining import MiningLookup
from tycho.knowledge.wiki import WikiError

NAMES = ["Quantainium (Raw)", "Lindinium (Ore)", "Raw Ice", "Hadanite", "Gold"]


def _location(slug: str, name: str, kind: str, parent: str, system: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "type": {"name": kind},
        "parent": {"name": parent},
        "system": f"{system} System",
    }


WHERE = {
    "Quantainium (Raw)": [
        _location("aberdeen", "Aberdeen", "Moon", "Hurston", "Stanton"),
        _location("lyria", "Lyria", "Moon", "ArcCorp", "Stanton"),
        _location("pyro-belt", "Akiro Cluster", "Asteroid", "Pyro", "Pyro"),
    ],
    "Lindinium (Ore)": [_location("glaciem", "Glaciem Ring", "Asteroid", "Nyx", "Nyx")],
}
CHANCES = {
    "aberdeen": (0.1, 0.01),
    "lyria": (0.2, 0.15),
    "pyro-belt": (0.1, 0.5),
    "glaciem": (0.1, 0.1),
}


class FakeMiningWiki:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.offline = False

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        query = dict(params or {})
        self.calls.append((path, query))
        if self.offline:
            raise WikiError("offline")
        if path == "/api/commodities":
            page = int(query.get("page[number]", 1))
            return {"data": [{"name": n} for n in NAMES] if page == 1 else []}
        if path == "/api/locations":
            return {"data": WHERE.get(str(query["filter[resource]"]), [])}
        slug = path.rsplit("/", 1)[-1]
        group, share = CHANCES[slug]
        resource = next(r for r, locs in WHERE.items() if any(loc["slug"] == slug for loc in locs))
        return {
            "data": {
                "resources": [
                    {
                        "mining_type": "Ship Mining",
                        "resources": [
                            {
                                "materials": [
                                    {
                                        "name": resource,
                                        "group_probability": group,
                                        "relative_probability": share,
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        }


def _run(lookup: MiningLookup, **arguments: str) -> dict[str, Any]:
    async def go() -> Any:
        await lookup.prepare()
        return await lookup.run({"resource": "", "place": "", **arguments})

    result = asyncio.run(go())
    assert not result.is_error
    return json.loads(result.content)


def test_a_resource_is_found_where_its_likeliest_first() -> None:
    result = _run(MiningLookup(FakeMiningWiki()), resource="quantainium")
    assert result["resource"] == "Quantainium (Raw)"
    assert [p["name"] for p in result["places"]] == ["Akiro Cluster", "Lyria", "Aberdeen"]
    assert result["places"][0]["chance_percent"] == 5.0
    assert result["places"][0]["mining"] == ["Ship Mining"]
    assert "Wiki" in result["source"]


def test_a_misspelt_name_is_the_nearest_of_the_games_names() -> None:
    """Speech-to-text writes "quantanium"; the game says Quantainium (Raw)."""
    result = _run(MiningLookup(FakeMiningWiki()), resource="raw quantanium")
    assert result["resource"] == "Quantainium (Raw)"


def test_a_place_keeps_to_its_system_planet_or_moon() -> None:
    lookup = MiningLookup(FakeMiningWiki())
    stanton = _run(lookup, resource="quantainium", place="Stanton")
    assert [p["name"] for p in stanton["places"]] == ["Lyria", "Aberdeen"]
    hurston = _run(lookup, resource="quantainium", place="Hurston")
    assert [p["name"] for p in hurston["places"]] == ["Aberdeen"]
    assert hurston["places"][0]["around"] == "Hurston, Stanton"


def test_a_resource_not_in_the_place_says_where_it_is() -> None:
    result = _run(MiningLookup(FakeMiningWiki()), resource="lindinium", place="Stanton")
    assert "isn't found in Stanton" in result["not_found"]
    assert result["found_in_systems"] == ["Nyx System"]


def test_a_name_only_a_bit_like_a_resource_isnt_taken_for_it() -> None:
    """ "unobtainium" is 0.82 like "quantainium": too far to be a misspelling."""
    result = _run(MiningLookup(FakeMiningWiki()), resource="unobtainium")
    assert "resource" not in result or result["resource"] != "Quantainium (Raw)"


def test_a_resource_nowhere_is_an_answer_not_an_error() -> None:
    result = _run(MiningLookup(FakeMiningWiki()), resource="unobtainium")
    assert "no location" in result["not_found"]


def test_without_the_name_list_the_games_forms_are_tried() -> None:
    wiki = FakeMiningWiki()
    lookup = MiningLookup(wiki)

    async def go() -> Any:
        wiki.offline = True
        await lookup.prepare()  # never raises
        wiki.offline = False
        return await lookup.run({"resource": "lindinium", "place": ""})

    result = json.loads(asyncio.run(go()).content)
    assert result["resource"] == "Lindinium (Ore)"


def test_what_was_fetched_is_kept_for_an_hour() -> None:
    now = [0.0]
    wiki = FakeMiningWiki()
    lookup = MiningLookup(wiki, clock=lambda: now[0])
    _run(lookup, resource="lindinium")
    fetched = len(wiki.calls)
    _run(lookup, resource="lindinium")
    assert len(wiki.calls) == fetched  # names, the list and the details: all kept
    now[0] = 4000.0
    _run(lookup, resource="lindinium")
    assert len(wiki.calls) == 2 * fetched  # an hour on: fetched again


def test_the_wiki_being_down_is_an_error() -> None:
    wiki = FakeMiningWiki()
    lookup = MiningLookup(wiki)
    asyncio.run(lookup.prepare())
    wiki.offline = True
    result = asyncio.run(lookup.run({"resource": "lindinium", "place": ""}))
    assert result.is_error
    assert "didn't answer" in result.content
