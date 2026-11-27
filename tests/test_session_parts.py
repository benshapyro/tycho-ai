"""Routing and planning, the pure parts the session and the brain eval share (#55)."""

import dataclasses
from pathlib import Path

import pytest

from tycho.brain import BrainError, Reply, RunActions, RunSequence
from tycho.commands import Command, CommandMatcher
from tycho.commands.keys import own_keys
from tycho.commands.reserved import TILE_WORDS
from tycho.games.star_citizen.gamelog import QUANTUM_ROUTE
from tycho.keybinds import Action, ActivationMode, Keybinds, KeyInput
from tycho.packs import STAR_CITIZEN, built_in
from tycho.runtime import Result
from tycho.runtime.conversation import Conversation
from tycho.runtime.planning import (
    CHAIN_BEAT_SECONDS,
    Questions,
    Refusal,
    Steps,
    announced,
    needs_facts,
    plan_request,
)
from tycho.runtime.pressing import TRIED, readable
from tycho.runtime.routing import (
    NotUnderstood,
    Picked,
    Reserved,
    ToBrain,
    from_brain,
    route,
    tile_command,
)
from tycho.text import states

SC = built_in(STAR_CITIZEN)

MATCHER = CommandMatcher.default(pack=SC)
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


def test_with_key_actions_off_a_built_in_phrase_goes_to_the_brain() -> None:
    # #384: the brain says how; what was matched is kept for when it can't answer.
    routed = route("landing gear", MATCHER, keys=False)
    assert isinstance(routed, ToBrain)
    assert [command.id for command in routed.matched] == [
        command.id for command in _commands("landing gear")
    ]


def test_own_keys_name_the_player_s_key_in_the_game_s_words() -> None:
    # #386: "Night Vision on Right Alt + N", not "your night vision key, if bound".
    action = Action(
        "v_toggle_night_vision",
        "spaceship_general",
        "Night Vision (Toggle)",
        (KeyInput("n", ("ralt",)),),
    )
    command = Command("night_vision", ("night vision",), ("v_toggle_night_vision",))
    assert own_keys((command,), Keybinds([action])) == "Night Vision on Right Alt + N"


def test_own_keys_say_when_a_command_has_no_key() -> None:
    other = Action("v_lights", "spaceship_general", "Lights", (KeyInput("l"),))
    command = Command("night_vision", ("night vision",), ("v_toggle_night_vision",))
    # Tycho sees only keyboard and mouse keys: it may be on the player's stick.
    assert (
        own_keys((command,), Keybinds([other]))
        == "night vision: no keyboard or mouse key Tycho can see"
    )


def test_own_keys_name_each_step_of_a_sequence() -> None:
    # #386 review: a sequence has no actions of its own, only steps.
    keys = own_keys(_commands("prepare to land"), KEYBINDS)
    assert keys == "v_atc_request on Left Alt + N; v_toggle_landing_system on N"


def test_own_keys_name_the_mode_switch_first() -> None:
    switch = Action("v_mode_scm", "spaceship_general", "SCM Mode", (KeyInput("b"),))
    main = Action("v_boost", "spaceship_general", "Boost", (KeyInput("lshift"),))
    command = Command("boost", ("boost",), ("v_boost",), setup=("v_mode_scm",))
    assert own_keys((command,), Keybinds([switch, main])) == (
        "SCM Mode on B, first; Boost on Left Shift"
    )


def test_own_keys_are_empty_when_tycho_reads_no_keybinds_for_the_game() -> None:
    # An advisor pack such as WoW: Forever: Tycho can't see its keys, so says nothing.
    command = Command("night_vision", ("night vision",), ("v_toggle_night_vision",))
    assert own_keys((command,), Keybinds([])) == ""


def test_with_key_actions_off_and_no_brain_a_built_in_phrase_stays_a_pick() -> None:
    # So the player still hears the gate's refusal.
    assert isinstance(route("landing gear", MATCHER, keys=False, brain=False), Picked)


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
    picked = from_brain(
        RunActions(("v_toggle_landing_system",), say="Gear down."), MATCHER, lists=SC.safety
    )
    assert isinstance(picked, Picked)
    assert picked.by_brain
    assert picked.say == "Gear down."
    assert plan_request(picked.commands, KEYBINDS, lists=SC.safety) == plan_request(
        _commands("gear down"), KEYBINDS, lists=SC.safety
    )


def test_the_brains_reply_passes_through() -> None:
    reply = Reply("About 3 million aUEC.")
    assert from_brain(reply, MATCHER, lists=SC.safety) is reply


def test_a_sequence_the_brain_made_up_is_an_error() -> None:
    with pytest.raises(BrainError):
        from_brain(RunSequence("make_coffee"), MATCHER, lists=SC.safety)


def test_an_action_the_brain_made_up_presses_nothing() -> None:
    picked = from_brain(RunActions(("v_make_coffee",)), MATCHER, lists=SC.safety)
    assert isinstance(picked, Picked)
    planned = plan_request(picked.commands, KEYBINDS, lists=SC.safety)
    assert isinstance(planned, Refusal)
    assert planned.status == "no_key"


# Planning


def test_a_chain_is_planned_whole_with_a_beat_between_actions() -> None:
    planned = plan_request(_commands("gear down and request landing"), KEYBINDS, lists=SC.safety)
    assert isinstance(planned, Steps)
    assert planned.actions == ("v_toggle_landing_system", "v_atc_request")
    assert [wait for wait, _ in planned.presses] == [0.0, CHAIN_BEAT_SECONDS]


def test_a_confirm_grade_action_asks_in_the_gates_own_words() -> None:
    planned = plan_request(_commands("eject"), KEYBINDS, lists=SC.safety)
    assert isinstance(planned, Questions)
    assert planned.question() == "v_eject? Say confirm."
    assert planned.question("Commander") == "v_eject? Say confirm, Commander."
    assert planned.detail.endswith(': hold push-to-talk and say "confirm"')


def test_once_confirmed_it_is_pressed() -> None:
    planned = plan_request(_commands("eject"), KEYBINDS, confirmed=True, lists=SC.safety)
    assert isinstance(planned, Steps)
    assert planned.actions == ("v_eject",)


def test_an_unbound_action_presses_nothing() -> None:
    planned = plan_request(_commands("gear down"), Keybinds([], MODES), lists=SC.safety)
    assert isinstance(planned, Refusal)
    assert planned.status == "no_key"


def _needs_a_route(command: Command) -> Command:
    return dataclasses.replace(command, requires=(QUANTUM_ROUTE,))


def test_a_missing_fact_is_refused_and_found_only_when_needed() -> None:
    gear = _commands("gear down")
    assert not needs_facts(gear)
    routed = (_needs_a_route(gear[0]),)
    assert needs_facts(routed)
    refused = plan_request(routed, KEYBINDS, lists=SC.safety)
    assert isinstance(refused, Refusal)
    assert refused.status == "refused"
    assert isinstance(
        plan_request(routed, KEYBINDS, frozenset({QUANTUM_ROUTE}), lists=SC.safety), Steps
    )


def test_a_refusal_outranks_any_question() -> None:
    commands = (*_commands("eject"), _needs_a_route(_commands("gear down")[0]))
    planned = plan_request(commands, KEYBINDS, lists=SC.safety)
    assert isinstance(planned, Refusal)
    assert planned.status == "refused"


def test_announced_only_when_the_game_announces_every_part() -> None:
    gear = _commands("gear down")[0]
    loud = dataclasses.replace(gear, announced=True)
    quiet = dataclasses.replace(gear, announced=False)
    assert announced((loud,))
    assert not announced((loud, quiet))


# --- Try it (#465) ----------------------------------------------------------------------


def test_an_action_with_no_label_reads_as_words_not_its_id() -> None:
    assert readable("v_toggle_landing_system") == "Toggle landing system"
    assert readable("ui_toggle_map") == "Ui toggle map"
    assert readable("eject") == "Eject"
    assert readable("_") == "_"


def test_a_try_it_result_isnt_a_follow_up_the_brain_takes_as_done() -> None:
    conversation = Conversation(lambda: 0.0)
    tried = Result(
        1,
        "done",
        text="gear down",
        commands=("landing_gear",),
        detail=TRIED,
        would_press=("Landing System (N)",),
    )
    conversation.heard(tried)
    (exchange,) = conversation.follow_ups()
    assert exchange.outcome.startswith("only tried landing_gear in setup")
    assert "nothing was pressed" in exchange.outcome
    assert "did " not in exchange.outcome


def test_going_back_and_forward_a_card_by_voice() -> None:
    """#491: "go back a card" and "next card" walk the cards that gave way."""
    from tests.fakes.fake_display import FakeDisplay
    from tycho.runtime.tiling import tile_word
    from tycho.tile import Card, Row

    display = FakeDisplay()
    for title in "AB":
        display.show(Card("list", title=title, rows=(Row("Everus Harbor", value="3,048"),)))
    assert tile_word(display, "forward", ("", None)) == "nothing on the Tile for that"
    assert tile_word(display, "again", ("", None)) == ""
    assert tile_word(display, "forward", ("", None)) == ""
    assert display.calls[-2:] == ["again", "forward"]


def test_the_last_card_comes_back_when_none_gave_way() -> None:
    """#565: "show me the last tile" 20 s after a card went up, with no strips, said
    "nothing on the Tile for that", and the retry searched again."""
    from tests.fakes.fake_display import FakeDisplay
    from tycho.runtime.tiling import tile_word
    from tycho.tile import Card, Row

    display = FakeDisplay()
    card = Card("list", title="Galaxy loadout", rows=(Row("Laser", value="S3"),))
    assert tile_word(display, "again", ("Two more on the Tile.", card)) == ""
    assert display.calls == ["again", "show list pinned"]
    assert display.shown == [card]
    # Words alone aren't a card to bring back: that's "show me that".
    assert tile_word(FakeDisplay(), "again", ("RSI.", None)) == "nothing on the Tile for that"


def test_a_follow_up_remembers_what_the_brain_searched_and_showed() -> None:
    """#564: told only what Tycho said, the brain denied searching, then searched the
    same thing twice more."""
    from tycho.tile import Card

    conversation = Conversation(lambda: 0.0)
    replied = Result(
        1,
        "replied",
        text="best galaxy loadout",
        by_brain=True,
        say="Two on the Tile.",
        card=Card("list", title="Galaxy loadout"),
        looked_up=('searched the web for "Galaxy loadout"',),
    )
    conversation.heard(replied)
    (exchange,) = conversation.follow_ups()
    assert exchange.outcome == (
        'searched the web for "Galaxy loadout"; showed a card on the Tile: Galaxy loadout; '
        "said: Two on the Tile."
    )


@pytest.mark.parametrize(
    "text",
    [
        "gear's down",
        "the power's on",
        "okay, lights\N{RIGHT SINGLE QUOTATION MARK}re on",
        "he's opening the doors",
    ],
)
def test_words_that_say_how_things_are_go_to_the_brain(text: str) -> None:
    """A free probe of trick phrasings (27 Nov 2026): "gear's down" and "power's on"
    were pressed as commands. Where only the toggle is bound, that raises the gear or
    powers the ship off."""
    assert isinstance(route(text, MATCHER), ToBrain)


@pytest.mark.parametrize(
    "text", ["gear down", "let's lower the gear", "target whoever's shooting at us"]
)
def test_a_request_with_an_apostrophe_still_takes_the_fast_path(text: str) -> None:
    assert isinstance(route(text, MATCHER), Picked)


def test_no_built_in_phrase_says_how_things_are() -> None:
    """``routing._written_so`` lets a stated phrase take the fast path only when its
    command was written that way, so no built-in phrase may be (#719)."""
    assert [p for c in MATCHER.commands for p in c.say if states(p)] == []


def test_a_players_own_stated_phrase_takes_the_fast_path(tmp_path: Path) -> None:
    """#719: "it's go time" in the player's own commands.toml went to the brain, which
    refused it as how things are, so the player's phrase never ran."""
    mine = tmp_path / "commands.toml"
    mine.write_text(
        '[[command]]\nid = "go_time"\nsay = ["it\'s go time", "we\'re landing"]\n'
        'actions = ["v_deploy_landing_system"]\n',
        "utf-8",
    )
    matcher = CommandMatcher.default(mine, pack=SC)
    for said in (
        "it's go time",
        "Tycho, we're landing",
        "we're landing please",
        "it's go time, thank you",
    ):
        picked = route(said, matcher)
        assert isinstance(picked, Picked), said
        assert [c.id for c in picked.commands] == ["go_time"]
    assert isinstance(route("gear's down", matcher), ToBrain)


@pytest.mark.parametrize("text", ["lights on?", "Doors open?", "gear down?", "okay, lights on?"])
def test_a_command_said_as_a_question_goes_to_the_brain(text: str) -> None:
    """#717: "lights on?" may ask whether they are; where only the toggle is bound,
    pressing it turned them off."""
    assert isinstance(route(text, MATCHER), ToBrain)


def test_without_a_brain_a_command_said_as_a_question_is_still_pressed() -> None:
    """There's nothing else to ask, and "lights on?" was pressed before #717."""
    assert isinstance(route("lights on?", MATCHER, brain=False), Picked)


@pytest.mark.parametrize(
    "text",
    [
        "can you open the doors?",
        "could you lower the gear?",
        "open the doors?",
        "lights on, please?",
        "please lights on?",
        "Tycho, please lights on?",
        "let's lower the gear?",
    ],
)
def test_a_polite_request_with_a_question_mark_keeps_the_fast_path(text: str) -> None:
    assert isinstance(route(text, MATCHER), Picked)


def test_with_key_actions_off_a_command_said_as_a_question_keeps_its_match() -> None:
    """Review of #722 (#725): the brain gets the player's keys for it (#386), and the
    gate's reason if the brain fails."""
    routed = route("lights on?", MATCHER, keys=False)
    assert isinstance(routed, ToBrain)
    assert routed.matched
