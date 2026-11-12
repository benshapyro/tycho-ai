"""Game packs: the loader, Star Citizen as the first pack, and a fake second game (ADR 18)."""

import hashlib
import logging
import re
import shutil
import tomllib
import typing
from dataclasses import replace
from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.fakes.fake_keys import FakeClock, FakeFocus, FakeKeySender
from tests.fakes.fake_pack import FOLDER, REGISTRY, FakeGameKeybinds, fake_pack
from tycho import paths, safety, settings
from tycho.app import _checks, game_pack, load_commands  # pyright: ignore[reportPrivateUsage]
from tycho.brain import RunActions
from tycho.brain.briefing import (
    bound_actions,
    briefing,
    core_sections,
    game_facts,
    instructions,
)
from tycho.brain.claude import ClaudeBrain
from tycho.commands import Command, CommandMatcher, Match
from tycho.game.keybinds import Action, Keybinds, KeyInput
from tycho.input import Presser, code_for
from tycho.knowledge.web import SITES
from tycho.lexicon import Lexicon
from tycho.packs import (
    BUILT_IN,
    CORE_SECTIONS,
    STAR_CITIZEN,
    GamePack,
    PackError,
    built_in,
    installed,
    load_pack,
)
from tycho.runtime.planning import Planned, Questions, Refusal, Steps, plan_request
from tycho.runtime.routing import Picked, from_brain
from tycho.settings import Game, Settings
from tycho.settings import rule as settings_rule
from tycho.tile.design import MAKER_ACCENTS, accent_for, accent_for_maker

STAR_CITIZEN_INSTRUCTIONS = "58cee8ce86e5f0871be300a6e735a1ae05a71563e17244a126897073d284329f"
"""The SHA-256 of Star Citizen's fixed briefing text, as it was before packs (main at
6645025). The prompt cache and the eval's scores depend on it staying byte for byte the
same. If you change the text on purpose, update this, and expect the cache to refill."""


# --- Star Citizen, the first pack --------------------------------------------------------


def test_star_citizens_briefing_is_byte_for_byte_what_it_was_before_packs() -> None:
    text = instructions(built_in(STAR_CITIZEN))
    assert hashlib.sha256(text.encode()).hexdigest() == STAR_CITIZEN_INSTRUCTIONS
    assert instructions() == text  # no pack named: Star Citizen


def test_star_citizen_is_the_pack_tycho_ships() -> None:
    pack = built_in()
    assert installed() == ("star-citizen",)
    assert (pack.id, pack.name, pack.version, pack.format) == (
        "star-citizen",
        "Star Citizen",
        "1.0.0",
        1,
    )
    assert (pack.mode, pack.window, pack.built_in) == ("keys", "StarCitizen.exe", True)
    assert pack.parts.keybinds == "star-citizen"
    assert set(pack.parts.lookups) == BUILT_IN.lookups
    assert pack.commands is not None
    assert "[[sequence]]" in pack.commands


def test_star_citizens_data_reaches_the_parts_that_used_it() -> None:
    pack = built_in()
    assert pack.web_sites == SITES
    assert "robertsspaceindustries.com" in SITES
    assert pack.tile.accents == MAKER_ACCENTS
    assert accent_for("DRAK_Cutlass_Black") == "#f5901e"
    assert accent_for_maker("Drake Interplanetary") == "#f5901e"
    assert game_facts() == pack.facts
    assert pack.facts
    assert len(Lexicon.default()) == len(pack.terms) > 100
    assert CommandMatcher.default().get("landing_gear") is not None


def test_the_core_of_the_briefing_has_every_section_a_pack_must_place() -> None:
    assert tuple(core_sections()) == CORE_SECTIONS


# --- Fake Game: a second game through the same seam -------------------------------------


def test_a_second_game_loads_with_its_own_parts() -> None:
    pack = fake_pack()
    assert (pack.id, pack.mode, pack.window, pack.built_in) == (
        "fake-game",
        "keys",
        "FakeGame.exe",
        False,
    )
    assert pack.web_sites == ("fakegame.example",)
    assert pack.terms == ("Glimmerhold", "Quillback")


def test_a_pack_naming_a_part_tycho_doesnt_have_is_refused() -> None:
    with pytest.raises(PackError, match="no built-in keybinds part 'fake-game'"):
        load_pack(FOLDER, built_in=False, registry=BUILT_IN)


def test_the_seam_presses_keys_for_a_game_that_isnt_star_citizen() -> None:
    pack = fake_pack()
    keybinds = FakeGameKeybinds().keybinds
    matcher = CommandMatcher.default(pack=pack)
    assert CommandMatcher.default(pack=pack).get("landing_gear") is None  # none of Star Citizen's
    found = matcher.match("get ready")
    assert isinstance(found, Match)
    # The hatch asks first in Fake Game (its safety.toml); the player said "confirm".
    assert isinstance(plan_request(found.commands, keybinds, lists=pack.safety), Questions)
    planned = plan_request(found.commands, keybinds, confirmed=True, lists=pack.safety)
    assert isinstance(planned, Steps)
    assert planned.actions == ("shield_raise", "hatch_open")

    sender, clock = FakeKeySender(), FakeClock()
    presser = Presser(sender, FakeFocus(True), sleep=clock.sleep, clock=clock)
    for _, press in planned.presses:
        assert presser.press(press).done
    downs = [code for event, code in sender.events if event == "down"]
    assert downs == [code_for("g"), code_for("lctrl"), code_for("h")]
    assert not sender.held()


def test_a_second_games_briefing_has_the_core_rules_and_none_of_star_citizen() -> None:
    pack = fake_pack()
    keybinds = FakeGameKeybinds().keybinds
    text = briefing(keybinds, CommandMatcher.default(pack=pack), pack)
    assert text.startswith("You are Tycho, a voice helper in Fake Game")
    assert core_sections()["safety"] in text
    assert core_sections()["squad"] in text
    # The core's examples are still Star Citizen's ("Gear down", quantum in the safety
    # rule): rewording them would change Star Citizen's briefing, so that waits for a
    # second real game. The game itself isn't named.
    assert "Star Citizen" not in text
    assert "UEX" not in text
    assert "the hatch opens from inside only" in text
    # Its own filters: the photo mode map and held walking aren't offered.
    offered = bound_actions(keybinds, pack=pack)
    assert "shield_raise" in offered
    assert "photo_take" not in offered
    assert "walk_forward" not in offered


def test_a_second_games_words_reach_the_speech_corrector() -> None:
    corrected = Lexicon.default(fake_pack()).correct("fly to glimmer hold")
    assert "Glimmerhold" in corrected.text


# --- What the loader refuses --------------------------------------------------------------


def _copy(tmp_path: Path) -> Path:
    folder = tmp_path / "fake-game"
    shutil.copytree(Path(str(FOLDER)), folder)
    return folder


def _edit(folder: Path, name: str, old: str, new: str) -> None:
    file = folder / name
    text = file.read_text("utf-8")
    assert old in text
    file.write_text(text.replace(old, new, 1), "utf-8")


@pytest.mark.parametrize(
    ("name", "old", "new", "message"),
    [
        ("pack.toml", "format = 1", "format = 2", "format 2 isn't one this Tycho reads"),
        ("pack.toml", 'id = "fake-game"', 'id = "Fake Game"', "lower-case letters"),
        ("pack.toml", 'version = "0.1.0"', 'version = "new"', "must be like 1.0.0"),
        ("pack.toml", 'mode = "keys"', 'mode = "both"', 'mode must be "keys" or "advisor"'),
        ("pack.toml", 'window = "FakeGame.exe"\n', "", "needs a keybinds part and a window"),
        ("pack.toml", 'mode = "keys"', 'mode = "advisor"', "an advisor pack can't have"),
        ("pack.toml", "[parts]", 'colour = "red"\n[parts]', "unknown settings: colour"),
        ("pack.toml", 'lookups = ["settings"]', 'lookups = ["horoscope"]', "no built-in look-up"),
        ("pack.toml", "fakegame.example", "https://fakegame.example/", "bare domains"),
        ("pack.toml", "'''walk_\\w+'''", "'''walk_(\\w+'''", "isn't a pattern"),
        ("briefing.md", "{{core:safety}}\n", "", "must all be placed; missing safety"),
        ("briefing.md", "{{core:tools}}", "{{core:tools}}\n\n{{core:tools}}", "more than once"),
        ("briefing.md", "{{core:tools}}", "{{core:rules}}", "no core section rules"),
        ("briefing.md", "{{core:voice}}", "Talk {{core:voice}}", "a line of its own"),
        ("commands.toml", "[[command]]", "[[command]", "commands.toml"),
    ],
)
def test_a_pack_that_isnt_right_is_refused_with_why(
    tmp_path: Path, name: str, old: str, new: str, message: str
) -> None:
    folder = _copy(tmp_path)
    _edit(folder, name, old, new)
    with pytest.raises(PackError, match=re.escape(message)):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_a_pack_holds_only_the_files_a_pack_can(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "reader.py").write_text("print('hi')", "utf-8")
    with pytest.raises(PackError, match=re.escape("files a pack can't hold: reader.py")):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_a_tile_accent_must_be_a_colour(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "tile.toml").write_text('[accents]\nFAKE = "orange"\n', "utf-8")
    with pytest.raises(PackError, match="accents must be colours"):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_an_advisor_pack_has_no_keys_to_press(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "commands.toml").unlink()
    _edit(folder, "pack.toml", 'mode = "keys"', 'mode = "advisor"')
    _edit(folder, "pack.toml", 'window = "FakeGame.exe"\n', "")
    _edit(folder, "pack.toml", 'keybinds = "fake-game"\n', "")
    pack = load_pack(folder, built_in=False, registry=REGISTRY)
    assert (pack.mode, pack.commands, pack.window, pack.parts.keybinds) == (
        "advisor",
        None,
        None,
        None,
    )
    assert CommandMatcher.default(pack=pack).commands == ()


def test_only_packs_tycho_ships_load_by_name() -> None:
    with pytest.raises(PackError, match="no game pack 'fake-game'"):
        built_in("fake-game")
    with pytest.raises(PackError, match="no game pack"):
        built_in("../tests")


# --- Safety: a pack can add danger, never remove it (#292) --------------------------------


def _plan(pack: GamePack, said: str, keybinds: Keybinds | None = None) -> Planned:
    found = CommandMatcher.default(pack=pack).match(said)
    assert isinstance(found, Match), said
    return plan_request(found.commands, keybinds or FakeGameKeybinds().keybinds, lists=pack.safety)


def test_star_citizens_lists_come_from_its_pack() -> None:
    lists = built_in().safety
    assert lists == safety.STAR_CITIZEN
    assert {"v_eject", "v_self_destruct", "attack1"} <= lists.confirm
    assert lists.travel == {"v_toggle_qdrive_engagement", "v_toggle_jump_request"}
    assert lists.mode_guards["v_master_mode_set_nav"] == {"spaceship_weapons", "spaceship_missiles"}
    assert safety.known_facts() == {"quantum_route_set"}


def test_a_packs_confirm_list_adds_danger() -> None:
    planned = _plan(fake_pack(), "open the hatch")
    assert isinstance(planned, Questions)
    assert planned.asking_about == ("Open Hatch",)
    # Star Citizen's lists don't know the hatch: without the pack's, it would just press.
    assert isinstance(plan_request(planned_commands("open the hatch"), _keys()), Steps)


def test_eject_without_asking_is_refused_by_the_backstop() -> None:
    # Fake Game leaves self_destruct out of its confirm list; the backstop still asks.
    pack = fake_pack()
    assert "self_destruct" not in pack.safety.confirm
    planned = _plan(pack, "blow it up")
    assert isinstance(planned, Questions)
    assert planned.asking_about == ("Self Destruct",)


@pytest.mark.parametrize(
    "line",
    [
        'not_danger = ["self_destruct"]',
        'accepted_risks = ["fake"]',
        '[mode_guards]\nshield_raise = ["fake"]',
        '[state_guards]\ndocked = ["fake"]',
    ],
)
def test_a_pack_from_outside_tycho_cant_loosen_the_gate(tmp_path: Path, line: str) -> None:
    folder = _copy(tmp_path)
    text = (folder / "safety.toml").read_text("utf-8")
    # Top-level lists go before the first table; tables go at the end.
    if line.startswith("["):
        text += "\n" + line + "\n"
    else:
        text = line + "\n" + text
    (folder / "safety.toml").write_text(text, "utf-8")
    name = line.removeprefix("[").split("]")[0].split(" ")[0]
    with pytest.raises(PackError, match=f"only a pack that ships inside Tycho may set {name}"):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_a_pack_that_ships_inside_tycho_may_name_harmless_actions(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    _edit(folder, "safety.toml", "travel =", 'not_danger = ["self_destruct"]\ntravel =')
    pack = load_pack(folder, built_in=True, registry=REGISTRY)
    assert isinstance(_plan(pack, "blow it up"), Steps)
    # The words a pack names are its own business, never a hole in another game's gate.
    assert isinstance(_plan(fake_pack(), "blow it up"), Questions)


def test_a_key_shared_with_a_dangerous_action_is_refused_with_the_packs_lists() -> None:
    keybinds = Keybinds([*FakeGameKeybinds().keybinds, _bound("hatch_vent", "g", "Vent Hatch")])
    pack = fake_pack()
    lists = replace(pack.safety, confirm=pack.safety.confirm | {"hatch_vent"})
    found = CommandMatcher.default(pack=pack).match("shield up")
    assert isinstance(found, Match)
    planned = plan_request(found.commands, keybinds, lists=lists)
    assert isinstance(planned, Refusal)
    assert "which is also Vent Hatch" in planned.detail


def test_a_packs_facts_say_what_is_missing() -> None:
    pack = fake_pack()
    command = Command("dock_up", ("dock up",), ("shield_raise",), requires=("docked",))
    keybinds = FakeGameKeybinds().keybinds
    action = keybinds.get("shield_raise")
    assert action is not None
    verdict = safety.check(command, action, keybinds, lists=pack.safety)
    assert verdict.reason == "you aren't docked"
    assert safety.check(command, action, keybinds, frozenset({"docked"}), lists=pack.safety).allowed
    assert safety.known_facts(pack.safety) == {"docked"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('ask_twice = ["x"]\n', "unknown lists: ask_twice"),
        ('[facts]\ndocked = ""\n', "needs the sentence"),
        ('accepted_risks = ["x"]\n[state_guards]\nflying = ["x"]\n', "doesn't list: flying"),
    ],
)
def test_safety_lists_that_arent_right_are_refused(tmp_path: Path, text: str, message: str) -> None:
    folder = _copy(tmp_path)
    (folder / "safety.toml").write_text(text, "utf-8")
    with pytest.raises(PackError, match=re.escape(message)):
        load_pack(folder, built_in=True, registry=REGISTRY)


def planned_commands(said: str) -> tuple[Command, ...]:
    found = CommandMatcher.default(pack=fake_pack()).match(said)
    assert isinstance(found, Match)
    return found.commands


def _keys() -> Keybinds:
    return FakeGameKeybinds().keybinds


def _bound(name: str, key: str, label: str) -> Action:
    return Action(name=name, actionmap="fake", label=label, inputs=(KeyInput(key),))


# --- The app wires the loaded pack (#293) -------------------------------------------------


def test_the_game_setting_offers_the_packs_tycho_ships() -> None:
    assert typing.get_args(Game.__value__) == installed()
    assert Settings().game == "star-citizen"
    assert settings_rule("game")["voice"] == "no"
    assert settings_rule("game")["applies"] == "restart"
    with pytest.raises(ValidationError):
        Settings.model_validate({"game": "fake-game"})


def test_an_old_settings_file_with_no_game_is_star_citizen(tmp_path: Path) -> None:
    file = tmp_path / "settings.toml"
    file.write_text("version = 1\nvoice_volume = 0.5\n", "utf-8")
    assert settings.load(file).game == "star-citizen"


def test_star_citizens_player_files_stay_where_they_were(tmp_path: Path) -> None:
    assert paths.commands_file() == paths.config_dir() / "commands.toml"
    assert paths.fleet_file("star-citizen") == paths.config_dir() / "fleet.toml"
    assert paths.commands_file("fake-game") == (
        paths.config_dir() / "packs" / "fake-game" / "commands.toml"
    )


def test_the_players_own_commands_are_per_game() -> None:
    own = paths.commands_file("fake-game")
    own.parent.mkdir(parents=True)
    own.write_text('[[phrases]]\ncommand = "raise_shield"\nsay = ["brace yourself"]\n', "utf-8")
    matcher = load_commands(fake_pack())
    assert isinstance(matcher.match("brace yourself"), Match)
    # Star Citizen doesn't read Fake Game's file.
    assert not isinstance(load_commands().match("brace yourself"), Match)


def test_tycho_check_names_the_game_and_its_pack_version() -> None:
    found = next(f for f in _checks(Settings(), quick=True, ask=False) if f.what == "Game")
    assert (found.level, found.detail) == ("ok", "Star Citizen (game pack 1.0.0)")


def test_the_brain_is_briefed_for_the_game_it_was_given() -> None:
    pack = fake_pack()
    brain = ClaudeBrain(CommandMatcher.default(pack=pack), api_key="test", pack=pack)
    brain._brief(FakeGameKeybinds().keybinds)  # pyright: ignore[reportPrivateUsage]
    text = brain._system[0]["text"]  # pyright: ignore[reportPrivateUsage]
    assert text.startswith("You are Tycho, a voice helper in Fake Game")
    assert "get_ready | raise_shield, then open_hatch | get ready" in text
    assert "shield_raise | raise the shield" in text


def test_travel_the_brain_picks_asks_first_by_the_packs_list() -> None:
    pack = fake_pack()
    matcher = CommandMatcher.default(pack=pack)
    picked = from_brain(RunActions(("warp_jump",), "Jumping."), matcher, pack.safety)
    assert isinstance(picked, Picked)
    assert picked.commands[0].confirm
    star_citizen = from_brain(RunActions(("warp_jump",), "Jumping."), matcher)
    assert isinstance(star_citizen, Picked)
    assert not star_citizen.commands[0].confirm


def test_the_start_log_names_the_pack_and_its_briefing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, "tycho")
    pack = game_pack(Settings())
    assert pack.id == "star-citizen"
    assert f"pack 1.0.0 (briefing {STAR_CITIZEN_INSTRUCTIONS[:12]})" in caplog.text


# --- The pack's own eval cases (#294) -----------------------------------------------------


def test_star_citizens_eval_cases_live_in_its_pack() -> None:
    folder = Path(str(resources.files("tycho.packs").joinpath("star-citizen")))
    cases = tomllib.loads((folder / "eval" / "cases.toml").read_text("utf-8"))
    assert len(cases["case"]) > 100
