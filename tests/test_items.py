"""Items (ADR 15): finding items by what players call them, the facts kept for each
kind, crafting recipes, and the look-up the brain calls."""

import asyncio
import json
from typing import Any

import httpx2
import pytest

from tests.fakes.fake_items import FakeItemsWiki
from tycho.knowledge import WikiApi, WikiError, items
from tycho.knowledge.item_lookup import ItemLookup
from tycho.knowledge.items import Found, Items, Missing, Unclear, facts, spellings, spoken, swapped

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


def _find(said: str, wiki: FakeItemsWiki | None = None) -> Found | Unclear | Missing:
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
    assert len(wiki.calls) == 1


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
        asyncio.run(api.get("/api/v2/items", {"filter[name]": "my secret gun"}))
    assert str(caught.value) == "/api/v2/items: HTTP 503"


@pytest.mark.parametrize(
    ("said", "written"),
    [
        ("S F nine", "SF9"),
        ("uh S F nine", "SF9"),
        ("F S nine", "FS9"),
        ("p four a r", "p4 ar"),
        ("the Atlas", "the Atlas"),  # nothing to do
        ("a Cutlass", "a Cutlass"),  # one letter alone is a word
    ],
)
def test_a_code_said_aloud_is_written_as_a_code(said: str, written: str) -> None:
    assert spoken(said) == written


def test_letters_said_quickly_are_tried_in_the_other_order() -> None:
    assert swapped("S F nine") == ["FS9"]
    assert swapped("fs9 lmg") == []  # typed or heard as one word: kept as it is
    assert len(swapped("A B C one")) == 5


def test_the_fs9_is_found_when_its_letters_are_heard_swapped() -> None:
    """Play session: "What do you need to craft the uh S F nine?" found nothing."""
    found = _find("S F nine")
    assert isinstance(found, Found)
    assert found.record["name"] == "FS-9 LMG"
