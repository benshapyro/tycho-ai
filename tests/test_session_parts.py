"""Routing and planning, the pure parts the session and the brain eval share (#55)."""

import dataclasses

import pytest

from tycho.brain import BrainError, Reply, RunActions, RunSequence
from tycho.commands import Command, CommandMatcher
from tycho.commands.reserved import TILE_WORDS
from tycho.game.gamelog import QUANTUM_ROUTE
from tycho.game.keybinds import Action, ActivationMode, Keybinds, KeyInput
from tycho.runtime.planning import (
    CHAIN_BEAT_SECONDS,
    Questions,
    Refusal,
    Steps,
    announced,
    needs_facts,
    plan_request,
)
from tycho.runtime.routing import (
    NotUnderstood,
    Picked,
    Reserved,
    ToBrain,
    from_brain,
    route,
    tile_command,
)

MATCHER = CommandMatcher.default()
MODES = {"press": ActivationMode("press")}


def _action(name: str, keys: str) -> Action:
    key = KeyInput.parse(keys)
    assert key is not None
    return Action(name=name, actionmap="m", label=name, inputs=(key,))


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n"),
        _action("v_atc_request", "lalt+n"),
        _action("v_eject", "ralt+y"),
    ],
    MODES,
)


def _commands(text: str) -> tuple[Command, ...]:
    routed = route(text, MATCHER)
    assert isinstance(routed, Picked), routed
    return routed.commands


# Routing


@pytest.mark.parametrize("what", sorted(TILE_WORDS))
def test_the_tiles_words_are_the_tiles_only_while_it_is_on(what: str) -> None:
    phrase = sorted(TILE_WORDS[what])[0]
    routed = route(phrase, MATCHER, tile=True)
    assert isinstance(routed, Reserved)
    assert routed.word == what
    assert not isinstance(route(phrase, MATCHER, tile=False), Reserved)


def test_every_tile_word_is_routed() -> None:
    assert {tile_command(next(iter(words))) for words in TILE_WORDS.values()} == set(TILE_WORDS)


@pytest.mark.parametrize(
    ("said", "word"),
    [("say that again", "say_again"), ("confirm", "confirm"), ("never mind", "cancel")],
)
@pytest.mark.parametrize("tile", [True, False])
def test_the_sessions_own_words_never_reach_the_fast_path_or_the_brain(
    said: str, word: str, tile: bool
) -> None:
    routed = route(said, MATCHER, tile=tile)
    assert isinstance(routed, Reserved)
    assert routed.word == word


def test_a_command_is_picked_by_the_fast_path() -> None:
    routed = route("gear down and request landing", MATCHER)
    assert isinstance(routed, Picked)
    assert [c.id for c in routed.commands] == ["landing_gear_down", "request_landing"]
    assert not routed.by_brain


def test_what_the_fast_path_cant_match_goes_to_the_brain_if_there_is_one() -> None:
    assert route("what ships are good", MATCHER) == ToBrain("no_match")
    assert route("what ships are good", MATCHER, brain=False) == NotUnderstood("no_match")


def test_the_brains_actions_become_commands_like_the_fast_paths() -> None:
    picked = from_brain(RunActions(("v_toggle_landing_system",), say="Gear down."), MATCHER)
    assert isinstance(picked, Picked)
    assert picked.by_brain
    assert picked.say == "Gear down."
    assert plan_request(picked.commands, KEYBINDS) == plan_request(_commands("gear down"), KEYBINDS)


def test_the_brains_reply_passes_through() -> None:
    reply = Reply("About 3 million aUEC.")
    assert from_brain(reply, MATCHER) is reply


def test_a_sequence_the_brain_made_up_is_an_error() -> None:
    with pytest.raises(BrainError):
        from_brain(RunSequence("make_coffee"), MATCHER)


def test_an_action_the_brain_made_up_presses_nothing() -> None:
    picked = from_brain(RunActions(("v_make_coffee",)), MATCHER)
    assert isinstance(picked, Picked)
    planned = plan_request(picked.commands, KEYBINDS)
    assert isinstance(planned, Refusal)
    assert planned.status == "no_key"


# Planning


def test_a_chain_is_planned_whole_with_a_beat_between_actions() -> None:
    planned = plan_request(_commands("gear down and request landing"), KEYBINDS)
    assert isinstance(planned, Steps)
    assert planned.actions == ("v_toggle_landing_system", "v_atc_request")
    assert [wait for wait, _ in planned.presses] == [0.0, CHAIN_BEAT_SECONDS]


def test_a_confirm_grade_action_asks_in_the_gates_own_words() -> None:
    planned = plan_request(_commands("eject"), KEYBINDS)
    assert isinstance(planned, Questions)
    assert planned.question() == "v_eject? Say confirm."
    assert planned.question("Commander") == "v_eject? Say confirm, Commander."
    assert planned.detail.endswith(': hold push-to-talk and say "confirm"')


def test_once_confirmed_it_is_pressed() -> None:
    planned = plan_request(_commands("eject"), KEYBINDS, confirmed=True)
    assert isinstance(planned, Steps)
    assert planned.actions == ("v_eject",)


def test_an_unbound_action_presses_nothing() -> None:
    planned = plan_request(_commands("gear down"), Keybinds([], MODES))
    assert isinstance(planned, Refusal)
    assert planned.status == "no_key"


def _needs_a_route(command: Command) -> Command:
    return dataclasses.replace(command, requires=(QUANTUM_ROUTE,))


def test_a_missing_fact_is_refused_and_found_only_when_needed() -> None:
    gear = _commands("gear down")
    assert not needs_facts(gear)
    routed = (_needs_a_route(gear[0]),)
    assert needs_facts(routed)
    refused = plan_request(routed, KEYBINDS)
    assert isinstance(refused, Refusal)
    assert refused.status == "refused"
    assert isinstance(plan_request(routed, KEYBINDS, frozenset({QUANTUM_ROUTE})), Steps)


def test_a_refusal_outranks_any_question() -> None:
    commands = (*_commands("eject"), _needs_a_route(_commands("gear down")[0]))
    planned = plan_request(commands, KEYBINDS)
    assert isinstance(planned, Refusal)
    assert planned.status == "refused"


def test_announced_only_when_the_game_announces_every_part() -> None:
    gear = _commands("gear down")[0]
    loud = dataclasses.replace(gear, announced=True)
    quiet = dataclasses.replace(gear, announced=False)
    assert announced((loud,))
    assert not announced((loud, quiet))
