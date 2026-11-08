"""Items (ADR 15): finding items by what players call them, the facts kept for each
kind, crafting recipes, and the look-up the brain calls."""

import asyncio
import json
from typing import Any

import httpx2
import pytest

from tests.fakes.fake_items import FakeItemsWiki, item
from tests.fakes.fake_wiki import search_answer
from tycho.knowledge import WikiApi, WikiError, items
from tycho.knowledge.item_lookup import ItemLookup
from tycho.knowledge.items import (
    Found,
    IsAShip,
    Items,
    Missing,
    Unclear,
    facts,
    search_text,
    spellings,
    spoken,
    swapped,
)
from tycho.knowledge.wiki import Hit, search

# --- Names --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "tries"),
    [
        ("FS-9", ["FS-9"]),
        ("fs9 lmg", ["fs9 lmg", "fs-9 lmg", "fs9-lmg"]),  # the code, as the API writes it
        ("arrow head", ["arrow head", "arrowhead", "arrow"]),  # two words run together
        ("p4 ar", ["p4 ar", "p-4 ar", "p4-ar"]),  # short pieces joined with a hyphen
        ("the Atlas quantum drive", ["Atlas quantum drive", "atlas"]),  # its distinctive word
    ],
)
def test_a_name_is_tried_in_the_spellings_the_api_needs(said: str, tries: list[str]) -> None:
    assert spellings(said) == tries


def _find(said: str, wiki: FakeItemsWiki | None = None) -> Found | Unclear | Missing | IsAShip:
    return asyncio.run(Items(wiki or FakeItemsWiki()).find(said))


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("FS-9", "FS-9 LMG"),  # not its magazine
        ("fs9 lmg", "FS-9 LMG"),
        ("arrow head", "Arrowhead Sniper Rifle"),  # not the Arrow Headhunters livery
        ("Atlas quantum drive", "Atlas"),  # the kind of item isn't in its name
        ("Glacier cooler", "Glacier"),  # not the Cambio-II "Glacier" SRT
        ("bulldog repeater", "CF-117 Bulldog Repeater"),
        ("p4 ar", "P4-AR Rifle"),
    ],
)
def test_items_are_found_by_what_players_call_them(said: str, expected: str) -> None:
    found = _find(said)
    assert isinstance(found, Found), found
    assert found.record["name"] == expected


def test_a_name_that_fits_several_items_lists_them() -> None:
    found = _find("Morozov")
    assert isinstance(found, Unclear)
    assert set(found.candidates) == {"Morozov-SH Helmet", "Morozov-SH Arms"}


def test_paint_and_cargo_are_never_items() -> None:
    found = _find("Atlasium")
    assert isinstance(found, Missing)


def test_an_unknown_name_is_missing() -> None:
    assert isinstance(_find("Unobtainium blaster"), Missing)


# --- Slow and missing answers -------------------------------------------------------------------


def test_a_slow_spelling_is_left_out_and_the_others_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(items, "QUERY_SECONDS", 0.05)
    wiki = FakeItemsWiki(slow=frozenset({"Atlas quantum drive"}))
    found = _find("Atlas quantum drive", wiki)
    assert isinstance(found, Found)
    assert found.record["name"] == "Atlas"


def test_when_every_spelling_is_too_slow_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(items, "QUERY_SECONDS", 0.05)
    with pytest.raises(WikiError, match="too long"):
        _find("Atlas", FakeItemsWiki(slow=frozenset({"Atlas"})))


def test_answers_are_reused_for_a_while() -> None:
    wiki = FakeItemsWiki()
    found = Items(wiki)

    async def twice() -> None:
        await found.find("Bulwark")
        await found.find("Bulwark")

    asyncio.run(twice())
    assert sorted(c["path"] for c in wiki.calls) == ["/api/items", "/api/search"]  # once each


# --- Facts --------------------------------------------------------------------------------------


def _item(name: str) -> Any:
    found = _find(name)
    assert isinstance(found, Found)
    return found.record


def test_an_fps_weapon_with_where_to_buy_and_how_to_craft() -> None:
    record = _item("FS-9 LMG")
    blueprint = asyncio.run(Items(FakeItemsWiki()).blueprint(record))
    assert facts(record, blueprint) == {
        "name": "FS-9 LMG",
        "maker": "Behring Applied Technology",
        "kind": "Medium Weapon",
        "size": 1,
        "weapon_type": "LMG",
        "damage_class": "Ballistic",
        "damage_per_shot": 14.5,
        "rounds_per_minute": 650,
        "dps": 157.1,
        "range_m": 1200,
        "magazine": 75,
        "buy_in_game": [
            {"where": "Guns Checkmate", "auec": 7029},
            {"where": "Guns Orbituary", "auec": 7100},
        ],
        "craftable": True,
        "crafting": {
            "ingredients": ["Lindinium 0.06 SCU", "Iron 0.03 SCU", "Dolivine x1"],
            "craft_time": "4 minutes",
            "known_from_start": False,
            "unlocked_by_missions": 21,
        },
    }


def test_ship_components_and_armor() -> None:
    shield = facts(_item("Bulwark"))
    assert (shield["shield_hp"], shield["regen_per_s"], shield["grade"]) == (2160, 410, "C")
    assert [s["auec"] for s in shield["buy_in_game"]] == [49500, 49500, 55129]  # type: ignore[index]
    drive = facts(_item("Atlas"))
    assert (drive["speed"], drive["spool_s"], drive["time_for_10_gm"]) == (
        "259.1 Mm/s",
        4.2,
        "0:55",
    )
    assert facts(_item("Glacier"))["coolant_segments"] == 38
    helmet = facts(_item("Morozov-SH Helmet"))
    assert helmet["damage_reduction_pct"] == {"physical": 40, "energy": 40, "stun": 60}


def test_a_ship_weapon_without_ammo_doesnt_say_zero() -> None:
    gun = facts(_item("bulldog repeater"))
    assert gun["dps"] == 218.7
    assert "ammo" not in gun
    assert "crafting" not in gun
    assert gun["craftable"] is False


# --- The look-up --------------------------------------------------------------------------------


def _run(arguments: dict[str, Any], wiki: FakeItemsWiki | None = None) -> tuple[bool, Any]:
    result = asyncio.run(ItemLookup(wiki or FakeItemsWiki()).run(arguments))
    return result.is_error, json.loads(result.content)


def test_it_answers_for_up_to_three_names() -> None:
    error, result = _run({"names": ["fs9 lmg", "Morozov", "Unobtainium", "Bulwark"]})
    assert not error
    assert result["source"].startswith("Star Citizen Wiki API")
    lmg, morozov, missing = result["items"]
    assert lmg["crafting"]["craft_time"] == "4 minutes"
    assert "matches" in morozov
    assert missing["error"] == "no item by that name in this patch's game files"


def test_without_the_api_it_says_so() -> None:
    wiki = FakeItemsWiki()
    wiki.offline = True
    error, result = _run({"names": ["Bulwark"]}, wiki)
    assert error
    assert "didn't answer" in result["error"]


@pytest.mark.parametrize("arguments", [{}, {"names": []}, {"names": "Bulwark"}])
def test_bad_arguments_say_what_to_pass(arguments: dict[str, Any]) -> None:
    error, result = _run(arguments)
    assert error
    assert "names" in result["error"]


def test_the_tool_schema_suits_strict_tool_use() -> None:
    lookup = ItemLookup(FakeItemsWiki())
    assert lookup.input_schema["additionalProperties"] is False
    assert lookup.seconds > 2 * items.QUERY_SECONDS  # a spelling, then the recipe


def test_a_failed_request_never_names_what_was_searched() -> None:
    # The URL holds the player's words; the error reaches the log (audit, 2026-11-03).
    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, request=request)

    client = httpx2.AsyncClient(
        base_url="https://api.star-citizen.wiki", transport=httpx2.MockTransport(refuse)
    )
    api = WikiApi(client)
    with pytest.raises(WikiError) as caught:
        asyncio.run(api.get("/api/items", {"filter[name]": "my secret gun"}))
    assert str(caught.value) == "/api/items: HTTP 503"


def test_a_client_can_be_closed() -> None:
    # The bench tools make new clients each round and close them (#213).
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(200)))
    asyncio.run(WikiApi(client).aclose())
    assert client.is_closed


@pytest.mark.parametrize(
    ("said", "written"),
    [
        ("S F nine", "SF9"),
        ("uh S F nine", "SF9"),
        ("F S nine", "FS9"),
        ("p four a r", "p4 ar"),
        ("the Atlas", "Atlas"),
        ("Cutlass", "Cutlass"),
        ("a S 38 pistol", "S38 pistol"),  # the article never starts a code (#174 review)
        ("the, uh, S F nine", "SF9"),  # filler with punctuation is dropped
    ],
)
def test_a_code_said_aloud_is_written_as_a_code(said: str, written: str) -> None:
    assert spoken(said) == written


def test_letters_said_quickly_are_tried_in_the_other_order() -> None:
    assert swapped("S F nine") == ["FS9"]
    assert swapped("fs9 lmg") == []  # typed or heard as one word: kept as it is
    assert len(swapped("X B C one")) == 5
    assert swapped("a S F nine") == ["FS9"]  # the article isn't a letter


def test_the_fs9_is_found_when_its_letters_are_heard_swapped() -> None:
    """Play session: "What do you need to craft the uh S F nine?" found nothing."""
    found = _find("S F nine")
    assert isinstance(found, Found)
    assert found.record["name"] == "FS-9 LMG"


def test_a_code_heard_in_the_right_order_beats_its_swapped_version() -> None:
    """Review of #174: with an FS-9 and an SF-9 both there, "F S nine" is the FS-9."""
    wiki = FakeItemsWiki(extra=[item("SF-9 Knife")])
    found = _find("F S nine", wiki)
    assert isinstance(found, Found)
    assert found.record["name"] == "FS-9 LMG"


# --- The Wiki API's unified search (#153) -------------------------------------------------------


def test_a_ship_asked_of_the_item_look_up_says_its_a_ship() -> None:
    """Live, item_lookup("Cutlass Black") answered with the Cutlass Black Plushie."""
    wiki = FakeItemsWiki(extra=[item("Cutlass Black Plushie", "Misc")])
    found = _find("Cutlass Black", wiki)
    assert found == IsAShip("Cutlass Black", "Drake Cutlass Black")
    error, result = _run({"names": ["Cutlass Black"]}, wiki)
    assert not error
    assert result["items"] == [
        {
            "asked": "Cutlass Black",
            "is_a_ship": "Drake Cutlass Black",
            "note": "a ship, not an item: use ship_lookup with this name",
        }
    ]


def test_a_ship_weapon_named_after_its_ship_is_the_weapon() -> None:
    """Review of #220: "Mantis gatling" was the RSI Mantis, as the kind of item
    ("gatling") was left out when the ship's name was compared."""
    wiki = FakeItemsWiki(extra=[item("Mantis GT-220 Gatling", "WeaponGun")])
    wiki.search.extra["mantis"] = {
        "data": [
            {
                "type": "vehicles",
                "label": "Vehicles",
                "results": [{"name": "RSI Mantis", "extra_label": "Combat"}],
            }
        ]
    }
    found = _find("Mantis gatling", wiki)
    assert isinstance(found, Found), found
    assert found.record["name"] == "Mantis GT-220 Gatling"


def test_a_miss_is_rescued_by_the_search(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every spelling's query is slow (new to the API), but the search answers.
    monkeypatch.setattr(items, "QUERY_SECONDS", 0.05)
    found = _find("fs-9", FakeItemsWiki(slow=frozenset({"fs-9"})))
    assert isinstance(found, Found)
    assert found.record["name"] == "FS-9 LMG"


def test_a_miss_offers_the_closest_names_the_search_found() -> None:
    # No Gallant in these items, so the search's names are the closest.
    found = _find("the Gallant")
    assert isinstance(found, Missing)
    assert found.closest[0] == "Gallant Rifle"
    assert len(found.closest) == 3


def test_a_slow_search_leaves_the_answer_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(items, "QUERY_SECONDS", 0.05)
    wiki = FakeItemsWiki(extra=[item("Cutlass Black Plushie", "Misc")])
    wiki.search.delay = 1.0
    found = _find("Bulwark", wiki)
    assert isinstance(found, Found)
    assert found.record["name"] == "Bulwark"
    assert _find("the Gallant", wiki) == Missing("the Gallant", ())


def test_a_failed_search_is_still_an_answer_not_a_failure() -> None:
    wiki = FakeItemsWiki()
    wiki.search.down = True
    error, result = _run({"names": ["Bulwark", "the Gallant"]}, wiki)
    assert not error
    bulwark, gallant = result["items"]
    assert bulwark["name"] == "Bulwark"
    assert gallant["error"] == "no item by that name in this patch's game files"


def test_the_search_is_asked_for_the_name_as_the_game_writes_it() -> None:
    assert search_text("the uh S F nine") == "sf-9"
    assert search_text("Atlas quantum drive") == "atlas"  # the kind of item isn't in its name
    assert search_text("quantum drive") == "quantum drive"  # unless that's all there is
    assert search_text("Cutlass Black") == "cutlass black"


class _Answers:
    """A Wiki source that answers every request with ``answer``, noting the params."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.params: list[dict[str, Any]] = []

    async def get(self, path: str, params: Any = None) -> Any:
        self.params.append({"path": path, **(params or {})})
        return self.answer


def test_the_search_sends_only_what_the_api_accepts() -> None:
    source = _Answers({"data": []})
    assert asyncio.run(search(source, 'Gallant "Midnight" Rifle!')) == []
    assert source.params == [{"path": "/api/search", "filter[query]": "Gallant Midnight Rifle"}]
    assert asyncio.run(search(source, "a")) == []  # too short to ask
    assert len(source.params) == 1


def test_the_search_reads_each_group() -> None:
    hits = asyncio.run(search(_Answers(search_answer("cutlass black")), "cutlass black"))
    assert hits[0] == Hit("vehicles", "Drake Cutlass Black", "Multi-Role")
    assert hits[-1] == Hit("items", "Cutlass Black Plushie", "Misc")


@pytest.mark.parametrize(
    "answer",
    [
        [],
        {"data": {"type": "items"}},
        {"data": [{"type": "items"}]},
        {"data": [{"type": "items", "results": [{"name": None}]}]},
        {"data": [{"type": "items", "results": ["Gallant"]}]},
    ],
)
def test_a_surprising_search_answer_is_an_error_that_never_names_the_query(answer: Any) -> None:
    with pytest.raises(WikiError) as caught:
        asyncio.run(search(_Answers(answer), "my secret gun"))
    assert "secret" not in str(caught.value)


# --- The kind of item said (#233) ----------------------------------------------------------------


def test_an_item_of_another_kind_than_the_one_said_is_not_a_match() -> None:
    """#233, seen live: "Arrow pistol" was the Arrowhead Sniper Rifle (and the 'Arrow' I
    Missile); there's no Arrow pistol, so it's a miss that names them."""
    found = _find("Arrow pistol")
    assert isinstance(found, Missing), found
    assert "Arrowhead Sniper Rifle" in found.closest


@pytest.mark.parametrize("said", ["FS-9 rifle", "fs9 smg", "Arrowhead rifle"])
def test_a_near_kind_still_matches(said: str) -> None:
    """Review of #239: an LMG called a rifle was a miss; players say it that way."""
    assert isinstance(_find(said), Found), said


def test_a_broad_word_for_the_kind_counts_against_nothing() -> None:
    assert isinstance(found := _find("Arrowhead gun"), Found), found
    assert found.record["name"] == "Arrowhead Sniper Rifle"
    assert isinstance(found := _find("Arrowhead sniper"), Found), found


class _MantisWiki:
    """#233, as the live API answered: the name queries find the Mantis's flight
    blade, and only the search knows the Mantis GT-220 Gatling (as a blueprint)."""

    BLADE = item("Mantis Standard Flight Blade", "FlightController")
    GATLING = item("Mantis GT-220 Gatling", "WeaponGun")

    def __init__(self) -> None:
        self.searched: list[str] = []

    async def get(self, path: str, params: Any = None) -> Any:
        query = dict(params or {})
        if path == "/api/search":
            return {
                "data": [
                    {
                        "type": "blueprints",
                        "label": "Blueprints",
                        "results": [{"name": "Mantis GT-220 Gatling", "item_type_label": None}],
                    }
                ]
            }
        name = str(query.get("filter[name]", ""))
        if name == self.GATLING["name"]:
            return {"data": [self.GATLING]}
        return {"data": [self.BLADE] if "mantis" in name.lower() else []}


def test_an_item_the_search_knows_beats_a_worse_one_the_names_found() -> None:
    found = _find("Mantis gatling", _MantisWiki())  # pyright: ignore[reportArgumentType]
    assert isinstance(found, Found), found
    assert found.record["name"] == "Mantis GT-220 Gatling"


def test_items_of_one_name_are_one_answer_not_a_question() -> None:
    """#233, seen live: "Gladius decoy launcher" asked which of three of the same name."""
    same = [
        item("Aegis Gladius - Decoy Launcher", "WeaponDefensive", uuid=f"decoy-{n}")
        for n in range(3)
    ]
    found = _find("Gladius decoy launcher", FakeItemsWiki(extra=same))
    assert isinstance(found, Found), found
    assert found.record["name"] == "Aegis Gladius - Decoy Launcher"
