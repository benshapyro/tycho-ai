import pytest

from tycho.commands import Command, CommandMatcher, Defer, Match, resolve
from tycho.game.keybinds import Action, Keybinds, KeyInput

MATCHER = CommandMatcher.default()


def _ids(text: str) -> str:
    result = MATCHER.match(text)
    return "brain" if isinstance(result, Defer) else "+".join(c.id for c in result.commands)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Wordings that aren't in commands.toml, but should still work.
        ("Okay, lights off please.", "lights_off"),
        ("could you close the doors", "doors_close"),
        ("Shields the front.", "shields_front"),
        ("hey tycho gear down", "landing_gear_down"),
        ("Lower the landing gear now.", "landing_gear_down"),
        ("open the doors then lights on", "doors_open+lights_on"),
        ("flight ready and request landing", "flight_ready+request_landing"),
    ],
)
def test_matches_unseen_wordings(text: str, expected: str) -> None:
    assert _ids(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "get us ready to leave and call the tower",  # one part isn't a known command
        "do that again",
        "lights of",  # a mishearing that could mean on or off
        "what's the nearest refinery",
        "set a route to microTech",
        "",
    ],
)
def test_defers_what_it_cannot_be_sure_of(text: str) -> None:
    assert _ids(text) == "brain"


@pytest.mark.parametrize(
    ("text", "never"),
    [
        ("lights off", "lights_on"),
        ("shields to back", "shields_front"),
        ("gear up", "landing_gear_down"),
        ("power off", "power_on"),
        ("lock the doors", "doors_unlock"),
        ("engines off", "engines_on"),
    ],
)
def test_never_flips_meaning(text: str, never: str) -> None:
    assert never not in _ids(text).split("+")


@pytest.mark.parametrize(
    "text",
    [
        "don't target the nearest hostile",
        "dont request landing permission",
        "do not open the doors",
        "no, lights on",
        "never mind, gear down",
        "cancel landing gear",
        "stop, eject",
    ],
)
def test_negations_always_defer(text: str) -> None:
    result = MATCHER.match(text)
    assert isinstance(result, Defer)
    assert result.reason == "negation"


def test_a_close_runner_up_below_the_threshold_still_makes_it_ambiguous() -> None:
    matcher = CommandMatcher(
        [
            Command("a", ("open hangar alpha",), ("x",)),
            Command("b", ("open hangar beta",), ("y",)),
        ],
        threshold=0.85,
        margin=0.3,
    )
    # "alphb" scores 0.93 against alpha and 0.67 against beta: beta is below the
    # threshold, but within the margin, so the winner isn't trusted.
    result = matcher.match("open hangar alphb")
    assert isinstance(result, Defer)
    assert result.reason == "ambiguous"


def test_a_runner_up_outside_the_margin_does_not_block_the_winner() -> None:
    matcher = CommandMatcher(
        [
            Command("a", ("open hangar alpha",), ("x",)),
            Command("b", ("open hangar beta",), ("y",)),
        ],
        threshold=0.85,
        margin=0.2,
    )
    result = matcher.match("open hangar alphb")
    assert isinstance(result, Match)
    assert result.commands[0].id == "a"


@pytest.mark.parametrize(
    "phrase",
    ["gear and lights", "please gear down", "gear down now", "don't eject", ""],
)
def test_phrases_that_could_never_match_are_rejected(phrase: str) -> None:
    with pytest.raises(ValueError, match="can never match"):
        CommandMatcher([Command("x", (phrase,), ("a",))])


def test_near_misses_with_opposite_words_do_not_match() -> None:
    matcher = CommandMatcher([Command("on", ("lights on",), ("v_lights_on",))])
    assert isinstance(matcher.match("lights off"), Defer)


def test_ambiguous_near_matches_are_deferred() -> None:
    matcher = CommandMatcher(
        [
            Command("a", ("open hangar alpha",), ("x",)),
            Command("b", ("open hangar alphb",), ("y",)),
        ]
    )
    result = matcher.match("open hangar alphc")
    assert isinstance(result, Defer)
    assert result.reason == "ambiguous"


def test_duplicate_phrases_are_rejected() -> None:
    with pytest.raises(ValueError, match="both"):
        CommandMatcher([Command("a", ("gear",), ("x",)), Command("b", ("Gear!",), ("y",))])


def test_eject_needs_confirmation() -> None:
    result = MATCHER.match("eject")
    assert isinstance(result, Match)
    assert result.commands[0].confirm


def test_every_command_action_name_is_plausible() -> None:
    for command in MATCHER.commands:
        assert command.actions, command.id
        assert all(a == a.strip() and " " not in a for a in command.actions), command.id


def _action(name: str, key: str | None) -> Action:
    inputs = () if key is None else (KeyInput(key),)
    return Action(name=name, actionmap="m", label=name, inputs=inputs)


def test_resolve_prefers_the_specific_action() -> None:
    command = Command("doors_open", ("open the doors",), ("v_open_all_doors", "v_toggle_all_doors"))
    keybinds = Keybinds([_action("v_open_all_doors", "o"), _action("v_toggle_all_doors", "t")])
    resolved = resolve(command, keybinds)
    assert resolved.action is not None
    assert resolved.action.name == "v_open_all_doors"
    assert not resolved.fallback


def test_resolve_falls_back_to_the_toggle_and_says_so() -> None:
    command = Command("doors_open", ("open the doors",), ("v_open_all_doors", "v_toggle_all_doors"))
    keybinds = Keybinds([_action("v_open_all_doors", None), _action("v_toggle_all_doors", "t")])
    resolved = resolve(command, keybinds)
    assert resolved.action is not None
    assert resolved.action.name == "v_toggle_all_doors"
    assert resolved.fallback


def test_resolve_reports_no_key() -> None:
    command = Command("shields_front", ("shields to front",), ("v_shield_raise_level_forward",))
    assert (
        resolve(command, Keybinds([_action("v_shield_raise_level_forward", None)])).action is None
    )
