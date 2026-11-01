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
        ("shut it down", "shutdown"),
        ("shut the doors", "doors_close"),  # "shut" alone is still the doors
        ("start the engines", "engines_on"),
        ("power down", "power_off"),
    ],
)
def test_sequences_match_like_commands(text: str, expected: str) -> None:
    assert _ids(DEFAULT, text) == [expected]


def test_built_in_sequences_are_made_of_commands_with_short_waits() -> None:
    sequences = [c for c in DEFAULT.commands if c.is_sequence]
    assert {s.id for s in sequences} >= {"startup", "prepare_to_land", "shutdown"}
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
    ],
)
def test_a_broken_user_file_says_what_is_wrong(tmp_path: Path, text: str, problem: str) -> None:
    with pytest.raises(CommandFileError, match=problem):
        _user(tmp_path, text)
