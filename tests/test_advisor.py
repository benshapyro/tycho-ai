"""Advisor mode: a game pack, or the player, can turn key actions off (#191, ADR 18).

With key actions off, no path presses anything: not the fast path, a sequence, the
brain or a confirmation. The gate refuses first, the brain has no tool to press with,
and the briefing, ``tycho check`` and the window say which mode the game is in, and why.
"""

import asyncio
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fakes.fake_keys import FakeFocus, FakeKeySender
from tests.fakes.fake_pack import ADVISOR, REGISTRY, FakeGameKeybinds, advisor_pack, fake_pack
from tycho import safety, settings
from tycho.app import _checks, game_pack  # pyright: ignore[reportPrivateUsage]
from tycho.brain.briefing import briefing, core_sections, instructions
from tycho.brain.claude import ClaudeBrain
from tycho.brain.decisions import Decision, UnboundAction
from tycho.brain.openrouter import OpenRouterBrain
from tycho.brain.rounds import Round, Turn
from tycho.commands import CommandMatcher, Match
from tycho.game.keybinds import Action, Keybinds, KeyInput
from tycho.input import Presser, plan
from tycho.packs import YOU_TURNED_THEM_OFF, GamePack, PackError, as_run, built_in, load_pack
from tycho.runtime.planning import Refusal, plan_request
from tycho.settings import Settings
from tycho.settings import rule as settings_rule
from tycho.spoken_settings import Refused, check_rule

FAKE_ADVISOR = (
    "Tycho doesn't press keys in Fake Advisor. "
    "Its rules allow one action per keypress, made by the player."
)
STAR_CITIZEN_OFF = f"Tycho doesn't press keys in Star Citizen. {YOU_TURNED_THEM_OFF}"


def _star_citizen_off() -> GamePack:
    return as_run(built_in(), key_actions=False)


# --- The pack says so, and the player can only turn it off ---------------------------------


def test_an_advisor_pack_says_why_and_its_gate_lists_say_keys_are_off() -> None:
    pack = advisor_pack()
    assert (pack.mode, pack.why) == (
        "advisor",
        "Its rules allow one action per keypress, made by the player.",
    )
    assert pack.safety.keys_off == FAKE_ADVISOR
    assert not fake_pack().safety.keys_off
    assert not built_in().safety.keys_off


def test_an_advisor_pack_must_say_why(tmp_path: Path) -> None:
    folder = tmp_path / "fake-advisor"
    shutil.copytree(Path(str(ADVISOR)), folder)
    manifest = folder / "pack.toml"
    text = manifest.read_text("utf-8")
    manifest.write_text(re.sub(r"^why = .*\n", "", text, flags=re.MULTILINE), "utf-8")
    with pytest.raises(PackError, match="an advisor pack must say why"):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_a_pack_cant_turn_keys_off_or_on_from_its_safety_lists(tmp_path: Path) -> None:
    folder = tmp_path / "fake-advisor"
    shutil.copytree(Path(str(ADVISOR)), folder)
    (folder / "safety.toml").write_text('keys_off = ""\n', "utf-8")
    with pytest.raises(PackError, match="unknown lists: keys_off"):
        load_pack(folder, built_in=True, registry=REGISTRY)


def test_the_player_can_turn_key_actions_off_in_a_game_that_allows_them() -> None:
    pack = _star_citizen_off()
    assert (pack.mode, pack.why, pack.safety.keys_off) == (
        "advisor",
        YOU_TURNED_THEM_OFF,
        STAR_CITIZEN_OFF,
    )
    # The rest of its lists stay: "off" adds a refusal, it doesn't swap the lists.
    assert pack.safety.confirm == built_in().safety.confirm
    assert as_run(built_in(), key_actions=True) is built_in()


def test_the_player_cant_turn_key_actions_on_where_the_rules_forbid_them() -> None:
    pack = advisor_pack()
    assert as_run(pack, key_actions=True) is pack
    assert as_run(pack, key_actions=True).safety.keys_off == FAKE_ADVISOR


def test_the_key_actions_setting_can_go_off_by_voice_but_never_on() -> None:
    assert Settings().key_actions
    assert settings_rule("key_actions") == {
        "voice": "lower_only",
        "applies": "restart",
        "how": settings.FILE_OR_APP,
    }
    check_rule("key_actions", True, False)
    with pytest.raises(Refused, match="only goes down or off by voice"):
        check_rule("key_actions", False, True)


# --- No path presses ---------------------------------------------------------------------


def _bound(name: str, key: str) -> Action:
    parsed = KeyInput.parse(key)
    assert parsed is not None
    return Action(name=name, actionmap="m", label=name, inputs=(parsed,))


KEYS = Keybinds([_bound("v_toggle_landing_system", "n"), _bound("v_eject", "ralt+y")])


def test_the_gates_first_check_refuses_every_press_whatever_asks() -> None:
    lists = _star_citizen_off().safety
    matcher = CommandMatcher.default()
    for said, action in (("landing gear", "v_toggle_landing_system"), ("eject", "v_eject")):
        found = matcher.match(said)
        assert isinstance(found, Match)
        command, chosen = found.commands[0], KEYS.get(action)
        assert chosen is not None
        for confirmed in (False, True):
            verdict = safety.check(command, chosen, KEYS, confirmed=confirmed, lists=lists)
            # Refused, not asked: saying "confirm" can't change it.
            assert (verdict.allowed, verdict.ask, verdict.reason) == (
                False,
                False,
                STAR_CITIZEN_OFF,
            )
        # Ahead of every other check, even a missing fact.
        needs = replace(command, requires=("in_seat",))
        assert safety.check(needs, chosen, KEYS, lists=lists).reason == STAR_CITIZEN_OFF


@pytest.mark.parametrize("said", ["landing gear", "eject", "gear down and request landing"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_the_fast_path_and_confirm_plan_no_presses_with_keys_off(
    said: str, confirmed: bool
) -> None:
    found = CommandMatcher.default().match(said)
    assert isinstance(found, Match)
    planned = plan_request(
        found.commands, KEYS, confirmed=confirmed, lists=_star_citizen_off().safety
    )
    assert planned == Refusal("refused", STAR_CITIZEN_OFF)


def test_a_sequence_plans_no_presses_with_keys_off() -> None:
    pack = as_run(fake_pack(), key_actions=False)
    found = CommandMatcher.default(pack=pack).match("get ready")
    assert isinstance(found, Match)
    assert found.commands[0].is_sequence
    planned = plan_request(
        found.commands, FakeGameKeybinds().keybinds, confirmed=True, lists=pack.safety
    )
    assert isinstance(planned, Refusal)
    assert planned.detail == f"Tycho doesn't press keys in Fake Game. {YOU_TURNED_THEM_OFF}"


def test_an_advisor_packs_own_commands_press_nothing() -> None:
    # A player's own commands.toml for an advisor game: refused with why, not "no key".
    found = CommandMatcher.default().match("landing gear")
    assert isinstance(found, Match)
    planned = plan_request(found.commands, KEYS, lists=advisor_pack().safety)
    assert planned == Refusal("refused", FAKE_ADVISOR)


# --- The brain: no tool to press with, and a briefing that says why ----------------------


def _tool_names(tools: object) -> list[str]:
    return [
        str(t["name"]) if "name" in t else str(t["function"]["name"])  # type: ignore[index]
        for t in tools  # type: ignore[attr-defined]
    ]


def test_the_brain_has_no_tool_to_press_with_in_advisor_mode() -> None:
    for pack in (advisor_pack(), _star_citizen_off()):
        matcher = CommandMatcher.default(pack=pack)
        claude = ClaudeBrain(matcher, api_key="test", pack=pack, cards=lambda: True)
        names = _tool_names(claude._offered())  # pyright: ignore[reportPrivateUsage]
        assert not {"run_actions", "run_sequence"} & set(names)
        assert names[0] == "show_card"
        router = OpenRouterBrain(
            matcher, model="anthropic/claude-haiku-5.5", api_key="t", pack=pack
        )
        names = _tool_names(router._body([])["tools"])  # pyright: ignore[reportPrivateUsage]
        assert not {"run_actions", "run_sequence"} & set(names)
    # With key actions on, both are still offered, first.
    claude = ClaudeBrain(CommandMatcher.default(), api_key="test")
    assert _tool_names(claude._offered())[:2] == ["run_sequence", "run_actions"]  # pyright: ignore[reportPrivateUsage]


def test_a_press_the_brain_makes_up_anyway_is_refused() -> None:
    pack = _star_citizen_off()
    brain = ClaudeBrain(CommandMatcher.default(pack=pack), api_key="test", pack=pack)
    brain._brief(KEYS)  # pyright: ignore[reportPrivateUsage]
    actions = brain._actions  # pyright: ignore[reportPrivateUsage]
    sequences = brain._sequences  # pyright: ignore[reportPrivateUsage]
    assert (actions, sequences) == (frozenset(), [])

    async def pick() -> tuple[Decision | None, UnboundAction | None]:
        round_ = Round(Turn(), {}, actions, sequences, 0.0)
        decided = await round_.tool_call(
            "1", "run_actions", {"actions": ["v_eject"], "say": "Out."}
        )
        return decided, round_.turn.unbound

    decided, unbound = asyncio.run(pick())
    assert decided is None
    assert isinstance(unbound, UnboundAction)


def test_the_advisor_briefing_says_tycho_presses_nothing_and_why() -> None:
    pack = advisor_pack()
    text = briefing(Keybinds([]), CommandMatcher.default(pack=pack), pack)
    assert text.startswith("You are Tycho, a voice helper in Fake Advisor")
    assert f"Key actions are off: {FAKE_ADVISOR}" in text
    assert "Call run_actions" not in text
    assert core_sections()["tools"] not in text
    assert core_sections()["safety"] in text
    assert "Fake Advisor 2.0" in text
    assert "The player's bound actions" not in text


def test_with_keys_off_star_citizens_briefing_lists_nothing_to_press() -> None:
    pack = _star_citizen_off()
    text = briefing(KEYS, CommandMatcher.default(pack=pack), pack)
    assert f"Key actions are off: {STAR_CITIZEN_OFF}" in text
    assert "v_toggle_landing_system" not in text
    assert "The player's commands (the action it presses" not in text
    # With keys on, the text is the pinned one (tests/test_packs.py).
    assert instructions(built_in()) != instructions(pack)


# --- The app and tycho check say which mode, and why -------------------------------------


def test_tycho_check_says_key_actions_are_on_for_star_citizen() -> None:
    found = next(f for f in _checks(Settings(), quick=True, ask=False) if f.what == "Key actions")
    assert (found.level, found.detail) == ("ok", "on in Star Citizen: Tycho presses your keys")


def test_tycho_check_says_key_actions_are_off_and_why() -> None:
    current = Settings(key_actions=False)
    found = next(f for f in _checks(current, quick=True, ask=False) if f.what == "Key actions")
    assert found.level == "ok"
    assert found.detail.startswith("off in Star Citizen, so Tycho answers")
    assert found.detail.endswith(YOU_TURNED_THEM_OFF)
    assert "key_actions" in found.fix


def test_the_app_runs_the_game_as_the_setting_says() -> None:
    assert game_pack(Settings()).safety.keys_off == ""
    assert game_pack(Settings(key_actions=False)).safety.keys_off == STAR_CITIZEN_OFF


def test_nothing_focused_names_the_game() -> None:
    presser = Presser(FakeKeySender(), FakeFocus(False), game="Fake Game")
    receipt = presser.press(plan(_bound("hatch_open", "h"), {}))
    assert (receipt.outcome, receipt.detail) == ("no_focus", "Fake Game isn't focused")
