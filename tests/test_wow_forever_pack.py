"""World of Warcraft: Forever, the first advisor pack that ships (#300, ADR 18).

Data only, in Tycho's own words, with no look-ups (the maintainer's call after the
licence check, 12 November 2026): these tests keep it that way. It's for Forever, out
4 November 2026, not modern WoW or Classic (the maintainer, 13 November 2026), and it
mustn't sound like Star Citizen (#349)."""

import tomllib
from importlib import resources
from typing import Any

import pytest

from tycho.address import Address
from tycho.brain.briefing import instructions
from tycho.packs import built_in, installed
from tycho.settings import Settings

WOW = "wow-forever"
REFUSALS = ("don't press", "can't press", "doesn't press", "don't do", "can't do", "not press")


def _cases() -> list[dict[str, Any]]:
    folder = resources.files("tycho.packs").joinpath(WOW, "eval", "cases.toml")
    return tomllib.loads(folder.read_text("utf-8"))["case"]


def test_world_of_warcraft_ships_as_an_advisor() -> None:
    assert WOW in installed()
    pack = built_in(WOW)
    assert (pack.id, pack.name, pack.mode, pack.built_in) == (
        WOW,
        "World of Warcraft: Forever",
        "advisor",
        True,
    )
    assert "Blizzard" in pack.why
    assert pack.safety.keys_off.startswith(
        "Tycho doesn't press keys in World of Warcraft: Forever."
    )
    # Nothing that could press: no commands, keybinds, window, or loosened safety lists.
    assert (pack.commands, pack.parts.keybinds, pack.window) == (None, None, None)
    assert not pack.safety.not_danger
    assert not pack.safety.mode_guards
    assert not pack.safety.accepted_risks


def test_world_of_warcraft_looks_nothing_up() -> None:
    # No Blizzard API, guide site or wiki: only Tycho's own settings tool, and no web.
    pack = built_in(WOW)
    assert pack.parts.lookups == ("settings",)
    assert pack.web_sites == ()
    assert (pack.parts.install, pack.parts.game_state) == (None, None)


def test_the_briefing_is_the_advisors_and_says_why() -> None:
    text = instructions(built_in(WOW))
    assert "World of Warcraft: Forever" in text
    assert "Key actions are off" in text  # brain/advisor.md replaced the core's tool rules
    assert "Blizzard's rules don't allow tools that play the game for you" in text
    assert "Call run_actions" not in text  # the core's tool rules are replaced
    assert "Star Citizen" not in text


def test_its_facts_are_dated_and_its_words_are_game_words() -> None:
    pack = built_in(WOW)
    assert "Checked" in pack.facts
    assert {"Azeroth", "Skyborne", "Zephras Isle", "Krol'dok Stronghold"} <= set(pack.terms)
    assert "Warrior" not in pack.terms  # plain English stays as it was heard


def test_it_is_world_of_warcraft_forever_not_modern_wow() -> None:
    # The first version was written for modern WoW's Midnight expansion (#349).
    pack = built_in(WOW)
    assert "4 November 2026" in pack.facts
    assert "The level cap is 60" in pack.facts
    for modern in ("Haranir", "Devourer", "Dornogal", "Khaz Algar", "Skyriding", "Evoker"):
        assert modern not in pack.facts, modern
        assert modern not in pack.terms, modern
        assert modern not in pack.briefing, modern
    heard = {case["heard"] for case in _cases()}
    assert "what's the new demon hunter spec" not in heard
    assert "what's the level cap" in heard


@pytest.mark.parametrize("word", ["quantum", "Commander", "ship", "Star Citizen", "Gladius"])
def test_its_briefing_has_no_star_citizen_words(word: str) -> None:
    # The core's advisor sections are the ones every game shares (#349).
    assert word not in instructions(built_in(WOW))


def test_tycho_calls_the_player_nothing_here_unless_they_choose_a_name() -> None:
    pack = built_in(WOW)
    assert pack.call_me == ""
    assert Address.from_settings(Settings(), pack).header() == (
        "[Use no name or title for the player.]"
    )
    assert Address.from_settings(Settings(call_me="Ace"), pack).name == "Ace"
    assert Address.for_pack(pack).name == ""
    # Star Citizen keeps Tycho's own default.
    assert built_in().call_me is None
    assert Address.from_settings(Settings(), built_in()).name == "Commander"


def test_its_eval_fails_a_reply_with_another_game_s_words() -> None:
    folder = resources.files("tycho.packs").joinpath(WOW, "eval", "cases.toml")
    patterns = tomllib.loads(folder.read_text("utf-8"))["never_say"]
    joined = " ".join(patterns)
    assert "quantum" in joined
    assert "Commander" in joined


def test_its_eval_cases_never_expect_a_press() -> None:
    cases = _cases()
    assert len(cases) >= 25
    for case in cases:
        assert case["mode"]
        assert "actions" not in case["expect"], case["heard"]
        assert "must" not in case
    asks = [c for c in cases if c["tag"] == "press"]
    assert len(asks) >= 6
    for case in asks:
        assert case["expect"] == ["reply"]
        refusals = [w for group in case.get("mentions", []) for w in group]
        assert case["heard"] == "jump" or set(REFUSALS) <= set(refusals), case["heard"]
