"""The app's Commands page (ADR 17): the list, and the player's own phrases in their file."""

from pathlib import Path

import pytest

from tycho.commands import CommandMatcher
from tycho.desktop.commands import Commands, key_text
from tycho.game.keybinds import Action, Keybinds, KeyInput


def _action(name: str, key: str | None, label: str) -> Action:
    inputs = () if key is None else (KeyInput.parse(key) or KeyInput(key),)
    return Action(name=name, actionmap="m", label=label, inputs=inputs)


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n", "Landing System (Toggle)"),
        _action("v_eject", "ralt+y", "Eject"),
        _action("v_lights_on", None, "Headlights On"),
        _action("v_lights", "l", "Headlights (Toggle)"),
    ]
)

MINE = """# My own commands: keep this comment.
[[command]]
id = "space_mode"   # mine
say = ["space mode"]
actions = ["v_master_mode_set_scm"]
"""


@pytest.fixture
def user_file(tmp_path: Path) -> Path:
    return tmp_path / "commands.toml"


def _rows(commands: Commands) -> dict[str, dict[str, object]]:
    return {str(r["id"]): r for r in commands.rows()["rows"]}  # type: ignore[union-attr]


def test_each_command_shows_its_phrases_key_and_what_it_does(user_file: Path) -> None:
    rows = _rows(Commands(user_file, lambda: KEYBINDS))
    gear = rows["landing_gear"]
    assert gear["label"] == "Landing System (Toggle)"
    assert gear["key"] == "N"
    assert "landing gear" in gear["said"]  # type: ignore[operator]
    assert gear["mine"] == []
    assert rows["eject"]["confirm"] is True
    assert rows["eject"]["key"] == "Right Alt + Y"


def test_a_command_on_its_toggle_says_so_and_one_with_no_key_says_what_to_bind(
    user_file: Path,
) -> None:
    rows = _rows(Commands(user_file, lambda: KEYBINDS))
    assert rows["lights_on"]["key"] == "L"
    assert rows["lights_on"]["toggle"] is True  # its best action, Headlights On, has no key
    nothing = [r for r in rows.values() if not r["sequence"] and not r["key"]]
    assert nothing
    assert all(r["to_bind"] for r in nothing)


def test_sequences_show_their_steps(user_file: Path) -> None:
    start = _rows(Commands(user_file, lambda: KEYBINDS))["startup"]
    assert start["sequence"] is True
    assert start["steps"] == ["Flight ready", "Request departure"]


def test_without_the_game_the_list_still_shows(user_file: Path) -> None:
    data = Commands(user_file, lambda: None).rows()
    assert data["game"] is False
    assert len(data["rows"]) > 50  # type: ignore[arg-type]


def test_adding_a_phrase_to_a_built_in_adds_only_the_phrase(user_file: Path) -> None:
    user_file.write_text(MINE, "utf-8")
    commands = Commands(user_file, lambda: KEYBINDS)
    assert commands.add_phrase("landing_gear", "  Wheels  Down ") == {
        "ok": True,
        "applies": "restart",
    }
    text = user_file.read_text("utf-8")
    assert "# My own commands: keep this comment." in text  # the player's file, kept
    assert 'id = "space_mode"   # mine' in text
    assert "[[phrases]]" in text
    assert 'id = "landing_gear"' not in text  # no copy of the built-in (#134)
    assert "v_toggle_landing_system" not in text
    matcher = CommandMatcher.default(user_file)
    gear = matcher.get("landing_gear")
    assert gear is not None
    assert "wheels down" in gear.say
    assert "landing gear" in gear.say  # the built-in phrases too
    assert gear.actions == ("v_toggle_landing_system",)
    assert gear.announced is True  # everything else as the built-in has it
    assert _rows(commands)["landing_gear"]["mine"] == ["wheels down"]


def test_a_second_phrase_goes_on_the_same_entry(user_file: Path) -> None:
    commands = Commands(user_file, lambda: KEYBINDS)
    commands.add_phrase("landing_gear", "wheels down")
    commands.add_phrase("landing_gear", "drop the wheels")
    assert user_file.read_text("utf-8").count('command = "landing_gear"') == 1
    assert _rows(commands)["landing_gear"]["mine"] == ["wheels down", "drop the wheels"]


def test_a_phrase_on_the_players_own_command_goes_in_its_entry(user_file: Path) -> None:
    user_file.write_text(MINE, "utf-8")
    commands = Commands(user_file, lambda: KEYBINDS)
    assert commands.add_phrase("space_mode", "go to space")["ok"] is True
    assert "[[phrases]]" not in user_file.read_text("utf-8")
    assert _rows(commands)["space_mode"]["mine"] == ["space mode", "go to space"]


def test_a_sequence_takes_phrases_too(user_file: Path) -> None:
    commands = Commands(user_file, lambda: KEYBINDS)
    assert commands.add_phrase("startup", "wake her up")["ok"] is True
    start = CommandMatcher.default(user_file).get("startup")
    assert start is not None
    assert "wake her up" in start.say
    assert start.is_sequence


def test_a_built_in_phrase_can_be_given_to_another_command(user_file: Path) -> None:
    """A built-in's phrases aren't the player's, even after they've added to it (#134)."""
    commands = Commands(user_file, lambda: KEYBINDS)
    commands.add_phrase("lights_on", "torches")
    result = commands.add_phrase("landing_gear", "lights on")
    assert result == {"ok": True, "applies": "restart", "took_from": "lights_on"}
    lights = CommandMatcher.default(user_file).get("lights_on")
    assert lights is not None
    assert "lights on" not in lights.say
    assert "torches" in lights.say


def test_a_phrase_taken_from_another_built_in_says_whose_it_was(user_file: Path) -> None:
    result = Commands(user_file, lambda: KEYBINDS).add_phrase("landing_gear", "lights on")
    assert result == {"ok": True, "applies": "restart", "took_from": "lights_on"}


@pytest.mark.parametrize(
    ("command", "phrase", "problem"),
    [
        ("landing_gear", "", "Type the words"),
        ("landing_gear", "gear", "already one of its phrases"),
        ("landing_gear", "wheels down please", "filler"),
        ("landing_gear", "x" * 61, "under 60"),
        ("warp_drive", "engage", "no command called"),
        ("space_mode", "landing gear", ""),  # taken from the built-in: allowed
    ],
)
def test_phrases_that_cant_work_are_refused_and_nothing_is_written(
    user_file: Path, command: str, phrase: str, problem: str
) -> None:
    user_file.write_text(MINE, "utf-8")
    result = Commands(user_file, lambda: KEYBINDS).add_phrase(command, phrase)
    if not problem:
        assert result["ok"] is True
        return
    assert result["ok"] is False
    assert problem in str(result["problem"])
    assert user_file.read_text("utf-8") == MINE


def test_a_phrase_another_of_the_players_commands_has_is_refused(user_file: Path) -> None:
    user_file.write_text(MINE, "utf-8")
    result = Commands(user_file, lambda: KEYBINDS).add_phrase("landing_gear", "space mode")
    assert result["ok"] is False
    assert "another of your commands" in str(result["problem"])
    assert user_file.read_text("utf-8") == MINE


def test_removing_the_last_phrase_of_yours_leaves_the_built_in_as_it_is(
    user_file: Path,
) -> None:
    user_file.write_text(MINE, "utf-8")
    commands = Commands(user_file, lambda: KEYBINDS)
    commands.add_phrase("landing_gear", "wheels down")
    assert commands.remove_phrase("landing_gear", "wheels down") == {
        "ok": True,
        "applies": "restart",
    }
    text = user_file.read_text("utf-8")
    assert "wheels down" not in text
    assert 'id = "space_mode"   # mine' in text
    assert _rows(commands)["landing_gear"]["mine"] == []
    gear = CommandMatcher.default(user_file).get("landing_gear")
    assert gear is not None
    assert "landing gear" in gear.say


def test_removing_a_phrase_keeps_the_comments_below_it(user_file: Path) -> None:
    commands = Commands(user_file, lambda: KEYBINDS)
    commands.add_phrase("landing_gear", "wheels down")
    user_file.write_text(user_file.read_text("utf-8") + "\n# a note of mine\n" + MINE, "utf-8")
    assert commands.remove_phrase("landing_gear", "wheels down")["ok"] is True
    text = user_file.read_text("utf-8")
    assert "# a note of mine" in text
    assert "# My own commands: keep this comment." in text


def test_an_older_copy_of_a_built_in_keeps_its_built_in_phrases(user_file: Path) -> None:
    """A copy written before #134: its built-in phrases aren't the player's to remove."""
    user_file.write_text(
        '[[command]]\nid = "landing_gear"\nsay = ["landing gear", "wheels down"]\n'
        'actions = ["v_toggle_landing_system"]\n',
        "utf-8",
    )
    commands = Commands(user_file, lambda: KEYBINDS)
    assert _rows(commands)["landing_gear"]["mine"] == ["wheels down"]
    result = commands.remove_phrase("landing_gear", "landing gear")
    assert result == {"ok": False, "problem": "That isn't one of your phrases."}
    assert commands.remove_phrase("landing_gear", "wheels down")["ok"] is True


def test_removing_a_phrase_from_the_players_own_command_keeps_the_command(user_file: Path) -> None:
    user_file.write_text(MINE.replace('["space mode"]', '["space mode", "go to space"]'), "utf-8")
    commands = Commands(user_file, lambda: KEYBINDS)
    assert commands.remove_phrase("space_mode", "go to space")["ok"] is True
    assert _rows(commands)["space_mode"]["mine"] == ["space mode"]


def test_only_the_players_own_phrases_can_be_removed(user_file: Path) -> None:
    result = Commands(user_file, lambda: KEYBINDS).remove_phrase("landing_gear", "landing gear")
    assert result == {"ok": False, "problem": "That isn't one of your phrases."}
    assert not user_file.exists()


def test_a_broken_file_is_reported_not_rewritten(user_file: Path) -> None:
    user_file.write_text("[[command]\nid = ", "utf-8")
    commands = Commands(user_file, lambda: KEYBINDS)
    assert "can't be used" in str(commands.rows()["problem"])
    result = commands.add_phrase("landing_gear", "wheels down")
    assert result["ok"] is False
    assert "has a mistake" in str(result["problem"])
    assert user_file.read_text("utf-8") == "[[command]\nid = "


@pytest.mark.parametrize(
    ("key", "shown"),
    [
        ("ralt+o", "Right Alt + O"),
        ("np_8", "Numpad 8"),
        ("f5", "F5"),
        ("tab", "Tab"),
        ("mouse4", "Mouse 4"),
    ],
)
def test_keys_read_as_the_player_knows_them(key: str, shown: str) -> None:
    parsed = KeyInput.parse(key)
    assert parsed is not None
    assert key_text(parsed) == shown
