"""Sequences and the player's own commands file (ADR 14)."""

from pathlib import Path

import pytest

from tycho.commands import CommandFileError, CommandMatcher, Match

DEFAULT = CommandMatcher.default()


def _ids(matcher: CommandMatcher, text: str) -> list[str]:
    result = matcher.match(text)
    assert isinstance(result, Match), (text, result)
    return [c.id for c in result.commands]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("startup", "startup"),
        ("start up the ship please", "startup"),
        ("prepare for landing", "prepare_to_land"),
        ("shut it down", "power_off"),  # just the power, unless the player says more
        ("shut down the engines", "engines_off"),
        ("shut the doors", "doors_close"),  # "shut" alone is still the doors
        ("start the engines", "engines_on"),
        ("power down", "power_off"),
    ],
)
def test_sequences_match_like_commands(text: str, expected: str) -> None:
    assert _ids(DEFAULT, text) == [expected]


def test_built_in_sequences_are_made_of_commands_with_short_waits() -> None:
    sequences = [c for c in DEFAULT.commands if c.is_sequence]
    assert {s.id for s in sequences} >= {"startup", "prepare_to_land"}
    for sequence in sequences:
        assert not sequence.actions
        for step in sequence.steps:
            assert not step.command.is_sequence
            assert DEFAULT.get(step.command.id) is step.command
            assert 0 <= step.wait_seconds <= 3, sequence.id


def test_a_command_expands_to_itself() -> None:
    gear = DEFAULT.get("landing_gear")
    assert gear is not None
    assert [s.command for s in gear.expand()] == [gear]


def _user(tmp_path: Path, text: str) -> CommandMatcher:
    file = tmp_path / "commands.toml"
    file.write_text(text, "utf-8")
    return CommandMatcher.default(file)


def test_no_user_file_means_the_built_in_commands(tmp_path: Path) -> None:
    matcher = CommandMatcher.default(tmp_path / "missing.toml")
    assert [c.id for c in matcher.commands] == [c.id for c in DEFAULT.commands]


def test_the_player_can_add_a_sequence_of_their_own(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        """
[[sequence]]
id = "combat_ready"
say = ["combat ready", "battle stations"]
steps = ["shields_on", { command = "weapons_on", wait = 0.5 }]
""",
    )
    assert _ids(matcher, "battle stations") == ["combat_ready"]
    sequence = matcher.get("combat_ready")
    assert sequence is not None
    assert [(s.command.id, s.wait_seconds) for s in sequence.steps] == [
        ("shields_on", 0.0),
        ("weapons_on", 0.5),
    ]


def test_the_player_replaces_a_built_in_by_id(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        """
[[sequence]]
id = "startup"
say = ["wake up"]
steps = ["power_on", { command = "flight_ready", wait = 1.5 }]
""",
    )
    assert _ids(matcher, "wake up") == ["startup"]
    old = matcher.match("startup")  # its built-in phrases went with it
    assert not isinstance(old, Match) or [c.id for c in old.commands] != ["startup"]
    startup = matcher.get("startup")
    assert startup is not None
    assert [s.command.id for s in startup.steps] == ["power_on", "flight_ready"]


def test_a_players_phrase_is_taken_from_the_built_in_command(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        """
[[command]]
id = "my_gear"
say = ["gear down"]
actions = ["v_deploy_landing_system"]
""",
    )
    assert _ids(matcher, "gear down") == ["my_gear"]
    assert _ids(matcher, "lower the gear") == ["landing_gear_down"]  # its other phrases stay


def test_phrases_entries_add_to_a_command_without_replacing_it(tmp_path: Path) -> None:
    """#134: a ``[[phrases]]`` entry adds phrases; everything else stays built-in."""
    matcher = _user(
        tmp_path,
        """
[[phrases]]
command = "landing_gear_down"
say = ["wheels down", "Lower  The Gear"]

[[phrases]]
command = "startup"
say = ["wake her up"]
""",
    )
    built_in = DEFAULT.get("landing_gear_down")
    gear = matcher.get("landing_gear_down")
    assert built_in is not None
    assert gear is not None
    assert gear.actions == built_in.actions
    assert set(built_in.say) <= set(gear.say)  # its built-in phrases stay
    assert gear.say.count("lower the gear") + gear.say.count("Lower  The Gear") == 1
    assert _ids(matcher, "wheels down") == ["landing_gear_down"]
    assert _ids(matcher, "wake her up") == ["startup"]


def test_a_phrases_entry_takes_its_phrase_from_another_built_in(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        '[[phrases]]\ncommand = "my_gear"\nsay = ["gear down"]\n'
        '\n[[command]]\nid = "my_gear"\nsay = []\nactions = ["v_deploy_landing_system"]\n',
    )
    assert _ids(matcher, "gear down") == ["my_gear"]
    down = matcher.get("landing_gear_down")
    assert down is not None
    assert "gear down" not in down.say


def test_one_phrase_for_two_of_the_players_commands_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CommandFileError, match="is in both"):
        _user(
            tmp_path,
            '[[phrases]]\ncommand = "landing_gear_down"\nsay = ["xyzzy"]\n'
            '\n[[phrases]]\ncommand = "startup"\nsay = ["xyzzy"]\n',
        )


def test_replacing_a_command_changes_the_sequences_that_use_it(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        """
[[command]]
id = "landing_gear_down"
say = ["wheels down"]
actions = ["v_deploy_landing_system"]
""",
    )
    land = matcher.get("prepare_to_land")
    assert land is not None
    gear = land.steps[1].command
    assert gear.id == "landing_gear_down"
    assert gear.actions == ("v_deploy_landing_system",)  # no toggle fallback any more


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("this isn't toml", "commands.toml"),
        (
            '[[sequence]]\nid = "x"\nsay = ["xyzzy"]\nsteps = ["warp_drive"]',
            "'warp_drive' is unknown",
        ),
        ('[[sequence]]\nid = "x"\nsay = ["xyzzy"]\nsteps = ["startup"]', "can't contain sequences"),
        ('[[sequence]]\nid = "x"\nsay = ["xyzzy"]\nsteps = []', "has no steps"),
        (
            '[[sequence]]\nid = "x"\nsay = ["xyzzy"]\nsteps = [{command = "power_on", wait = 11}]',
            "not 0 to 10",
        ),
        ('[[command]]\nid = "x"\nsay = ["xyzzy"]', "actions"),
        ('[[command]]\nsay = ["xyzzy"]\nactions = ["a"]', "needs an id"),
        ('[[command]]\nid = "x"\nsay = ["dont xyzzy"]\nactions = ["a"]', "can never match"),
        ('[[command]]\nid = "x"\nsay = 5\nactions = ["a"]', "say must be a list of text"),
        ('[[command]]\nid = "x"\nsay = "jump"\nactions = ["a"]', "say must be a list"),
        ('[[command]]\nid = "x"\nsay = ["xyzzy"]\nactions = "a"', "actions must be a list"),
        (
            '[[command]]\nid = "x"\nsay = ["xyzzy"]\nactions = ["a"]\nconfirm = "false"',
            "true or false",
        ),
        (
            '[[command]]\nid = "x"\nsay = ["xyzzy"]\nactions = ["a"]\nsettle_seconds = 1e9',
            "0 to 10",
        ),
        (
            '[[command]]\nid = "x"\nsay = ["xyzzy"]\nactions = ["a"]\nsettle_seconds = nan',
            "0 to 10",
        ),
        ('[[command]]\nid = 7\nsay = ["xyzzy"]\nactions = ["a"]', "id must be some text"),
        (
            '[[command]]\nid = "x"\nsay = ["xyzzy"]\nactions = ["a"]\nrequires = ["quantum_route"]',
            "requires has 'quantum_route', which Tycho doesn't know; it can be 'quantum_route_set'",
        ),
        (
            '[[sequence]]\nid = "x"\nsay = ["xyzzy"]\nrequires = ["route"]\nsteps = ["power_on"]',
            "requires has 'route'",
        ),
        (
            '[[command]]\nid = "x"\nsay = ["xyzzy"]\nactions = ["a"]\naccepted_risks = ["foot"]',
            "accepted_risks has 'foot', which Tycho doesn't know; it can be 'player'",
        ),
        ('[[phrases]]\nsay = ["xyzzy"]', "needs a command"),
        ('[[phrases]]\ncommand = "landing_gear"\nsay = "xyzzy"', "say must be a list of text"),
        ('[[phrases]]\ncommand = "warp_drive"\nsay = ["xyzzy"]', "no command with that id"),
        ("phrases = 5", "list of tables"),
    ],
)
def test_a_broken_user_file_says_what_is_wrong(tmp_path: Path, text: str, problem: str) -> None:
    with pytest.raises(CommandFileError, match=problem):
        _user(tmp_path, text)


def test_a_sequences_confirm_and_requires_reach_every_step(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        """
[[sequence]]
id = "careful_landing"
say = ["careful landing"]
confirm = true
requires = ["quantum_route_set"]
steps = ["landing_gear", "request_landing"]
""",
    )
    sequence = matcher.get("careful_landing")
    assert sequence is not None
    assert sequence.confirm
    assert sequence.requires == ("quantum_route_set",)
    assert [(s.command.confirm, s.command.requires) for s in sequence.steps] == [
        (True, ("quantum_route_set",)),
        (True, ("quantum_route_set",)),
    ]
    assert matcher.warnings == ()
    plain = matcher.get("landing_gear")
    assert plain is not None
    assert not plain.confirm  # the step command on its own is unchanged


def test_keys_a_sequence_cant_use_are_ignored_with_a_warning(tmp_path: Path) -> None:
    matcher = _user(
        tmp_path,
        """
[[command]]
id = "my_lights"
say = ["cabin glow"]
actions = ["v_lights"]

[[sequence]]
id = "quiet_start"
say = ["quiet start"]
announced = true
setup = ["v_power_toggle"]
steps = ["power_on"]
""",
    )
    assert matcher.get("my_lights") is not None  # the rest of the file still loads
    (warning,) = matcher.warnings
    assert "quiet_start" in warning
    assert "setup, announced" in warning
