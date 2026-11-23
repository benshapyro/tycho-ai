"""An update never breaks a player's files (#612, a 1.0 check).

``tests/fixtures/upgrades/<version>/`` holds each file a player keeps as that released
Tycho wrote it, made by that version's own code (``tools/upgrades/make_fixtures.py``);
``main`` is the version not released yet. Today's code must load every one with nothing
lost and no error, and keep what it doesn't know when it writes the file again.
"""

import datetime
import json
import logging
import re
import shutil
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest

from tycho import settings
from tycho.commands import CommandFileError, CommandMatcher
from tycho.games.star_citizen.fleet import MAX_SHIPS, Fleet, FleetFileError
from tycho.games.star_citizen.owned import OwnedBlueprints
from tycho.history import HistoryFolder
from tycho.memory import MemoryFileError, NoteFile
from tycho.packs import STAR_CITIZEN, built_in
from tycho.voice.ducking import Ducker

FIXTURES = Path(__file__).parent / "fixtures" / "upgrades"
CHANGELOG = Path(__file__).parents[1] / "CHANGELOG.md"
FIRST = (0, 5, 0)
"""The oldest version checked: the first with today's settings file and phrases file."""


def _released() -> list[str]:
    """Every version the changelog lists, from ``FIRST`` on."""
    found = re.findall(r"^## \[(\d+)\.(\d+)\.(\d+)\]", CHANGELOG.read_text("utf-8"), re.M)
    return [".".join(v) for v in found if tuple(map(int, v)) >= FIRST]


VERSIONS = (*_released(), "main")
SETTINGS = sorted(FIXTURES.glob("*/settings-*.toml"))
RENAMED = {("tile_colour", "ship"): "game"}
"""Values a later Tycho reads under a new name on purpose (#422)."""


def _now(name: str, value: object) -> object:
    return RENAMED.get((name, value), value) if isinstance(value, str) else value


def _named(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def test_every_released_version_has_fixtures() -> None:
    """A release without its files here isn't checked: after each release, run
    ``uv run python tools/upgrades/make_fixtures.py v<version>`` (the ``release`` skill)."""
    assert "0.10.0" in VERSIONS
    for version in VERSIONS:
        folder = FIXTURES / version
        assert (folder / "settings-default.toml").is_file(), version
        assert (folder / "settings-changed.toml").is_file(), version
        assert (folder / "commands.toml").is_file(), version
        assert (folder / "fleet.toml").is_file(), version


# --- settings.toml --------------------------------------------------------------------


@pytest.mark.parametrize("path", SETTINGS, ids=_named)
def test_settings_load_with_every_value_kept(path: Path) -> None:
    written = tomllib.loads(path.read_text("utf-8"))
    loaded = settings.load(path).model_dump(mode="json")
    for name, value in written.items():
        if name in loaded:  # a setting a later Tycho dropped is kept aside (below)
            assert loaded[name] == _now(name, value), name


@pytest.mark.parametrize("path", SETTINGS, ids=_named)
def test_a_change_keeps_the_rest_of_an_old_settings_file(path: Path, tmp_path: Path) -> None:
    copy = tmp_path / "settings.toml"
    shutil.copy(path, copy)
    before = tomllib.loads(copy.read_text("utf-8"))
    settings.update({"voice_volume": 0.4}, copy)
    after = tomllib.loads(copy.read_text("utf-8"))
    assert after["voice_volume"] == 0.4
    for name, value in before.items():
        if name not in ("voice_volume", "version"):
            assert after.get(name) == _now(name, value), name


def test_a_setting_from_a_newer_tycho_is_ignored_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    shutil.copy(FIXTURES / "main" / "settings-changed.toml", path)
    with path.open("a", encoding="utf-8") as f:
        f.write('\nsomething_new = "from 2.0"\n')
    assert settings.load(path).voice == "Sarah"
    assert settings.unknown_keys(path) == ("something_new",)
    settings.update({"voice_volume": 0.4}, path)
    assert tomllib.loads(path.read_text("utf-8"))["something_new"] == "from 2.0"


# --- commands.toml --------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_a_players_own_commands_still_load(version: str) -> None:
    """Every phrase the player gave a built-in command still runs that command."""
    folder = FIXTURES / version
    matcher = CommandMatcher.default(folder / "commands.toml", pack=built_in(STAR_CITIZEN))
    assert matcher.warnings == ()
    written = tomllib.loads((folder / "commands.toml").read_text("utf-8"))
    for entry in written["phrases"]:
        command = matcher.get(entry["command"])
        assert command is not None, f"{entry['command']} is gone"
        assert entry["say"][0] in command.say
    own = matcher.get("my_startup")
    assert own is not None
    assert own.is_sequence


# --- fleet.toml -----------------------------------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_the_fleet_loads_and_saves_with_every_ship(version: str, tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    shutil.copy(FIXTURES / version / "fleet.toml", path)
    written = tomllib.loads(path.read_text("utf-8"))
    fleet = Fleet.load(path)
    assert fleet.problem == ""
    assert fleet.ships == written["ships"]
    assert fleet.imported == written.get("imported", [])
    fleet.save()
    assert tomllib.loads(path.read_text("utf-8")) == written


# --- 0.11's new files -----------------------------------------------------------------


def test_memory_notes_load() -> None:
    every = NoteFile(FIXTURES / "main" / "memory.toml").notes()
    assert [n.text for n in every] == [
        "I play with a HOTAS and pedals",
        "Call quantum jumps 'spooling'",
    ]
    assert NoteFile(FIXTURES / "main" / "game-memory.toml").notes()[0].text == (
        "My home base is Area18"
    )


def test_owned_blueprints_load() -> None:
    owned = OwnedBlueprints(FIXTURES / "main" / "blueprints.toml", lambda: None)
    assert [(o.name, o.got) for o in owned.listed()] == [("FS-9 LMG", datetime.date(2026, 11, 23))]
    assert owned.problem == ""


def test_history_loads() -> None:
    folder = HistoryFolder(FIXTURES / "main" / "history")
    [played] = folder.sessions()
    entries = folder.entries(played.id)
    assert [e.said for e in entries] == ["lower the landing gear", "what's the best mining ship"]
    assert entries[0].did == ("v_toggle_landing_system",)


class _Volume:
    def __init__(self) -> None:
        self.set_to: list[dict[str, float]] = []

    def get(self) -> dict[str, float]:
        return {"StarCitizen.exe": 0.24}

    def set(self, levels: Mapping[str, float]) -> bool:
        self.set_to.append(dict(levels))
        return True


@pytest.mark.parametrize(
    "text", [(FIXTURES / "main" / "ducked.json").read_text("utf-8"), json.dumps({"level": 0.8})]
)
def test_a_lowered_game_volume_goes_back(text: str, tmp_path: Path) -> None:
    """Both shapes ``ducked.json`` has had: one level (before #578), and per program."""
    path = tmp_path / "ducked.json"
    path.write_text(text, "utf-8")
    volume = _Volume()
    Ducker(volume, path).recover()
    assert volume.set_to
    assert all(v == 0.8 for v in volume.set_to[0].values())
    assert not path.exists()


# --- broken files: kept, never a crash --------------------------------------------------

BROKEN = "this isn't [ TOML\n\x00"


def test_a_broken_settings_file_says_which_line_and_is_never_changed(tmp_path: Path) -> None:
    """Tycho won't guess settings (``key_actions`` among them): it says what's wrong and
    leaves the file exactly as it was, for the player to fix."""
    path = tmp_path / "settings.toml"
    path.write_text('voice_volume = 0.5\nkey_actions = "maybe"\n', "utf-8")
    with pytest.raises(settings.SettingsError, match="key_actions"):
        settings.load(path)
    with pytest.raises(settings.SettingsError):
        settings.update({"voice_volume": 0.4}, path)
    assert path.read_text("utf-8") == 'voice_volume = 0.5\nkey_actions = "maybe"\n'
    path.write_text(BROKEN, "utf-8")
    with pytest.raises(settings.SettingsError, match=str(path.name)):
        settings.load(path)
    assert path.read_text("utf-8") == BROKEN


def test_a_broken_commands_file_is_skipped_and_kept(tmp_path: Path) -> None:
    from tycho.desktop.commands import Commands
    from tycho.keybinds import Keybinds

    path = tmp_path / "commands.toml"
    path.write_text(BROKEN, "utf-8")
    pack = built_in(STAR_CITIZEN)
    with pytest.raises(CommandFileError, match=r"commands\.toml"):
        CommandMatcher.default(path, pack=pack)
    page = Commands(path, lambda: Keybinds([]), pack)
    assert "can't be used" in str(page.rows()["problem"])
    assert path.read_text("utf-8") == BROKEN


def test_a_broken_fleet_is_kept_and_never_overwritten(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text(BROKEN, "utf-8")
    with caplog.at_level(logging.WARNING):
        fleet = Fleet.load(path)
    assert "won't change it" in fleet.problem
    assert any("Starting without your fleet" in r.message for r in caplog.records)
    with pytest.raises(FleetFileError, match="won't change it"):
        fleet.save()
    assert path.read_text("utf-8") == BROKEN


def test_a_long_hand_written_fleet_loses_no_ship(tmp_path: Path) -> None:
    """The fleet tells the brain about 100 ships at most, but a longer list the player
    wrote keeps every ship when Tycho saves it (it cut the rest before)."""
    path = tmp_path / "fleet.toml"
    ships = [f"Ship {n}" for n in range(MAX_SHIPS + 20)]
    path.write_text("ships = [\n" + "".join(f'  "{s}",\n' for s in ships) + "]\n", "utf-8")
    fleet = Fleet.load(path)
    fleet.remove("Ship 0")
    fleet.save()
    assert tomllib.loads(path.read_text("utf-8"))["ships"] == ships[1:]
    assert fleet.describe(None).count("Ship ") == MAX_SHIPS


def test_a_fleet_broken_while_tycho_runs_is_not_written_over(tmp_path: Path) -> None:
    """Edited by hand mid-session into a shape Tycho can't read: the next change
    mustn't replace it. Fixed again, it's read as usual."""
    path = tmp_path / "fleet.toml"
    Fleet(path, ["Cutlass Black"]).save()
    running = Fleet.load(path)
    path.write_text('ships = "Cutlass Black", "Carrack"\n', "utf-8")
    running.refresh()
    assert running.ships == ["Cutlass Black"]  # still known this session
    with pytest.raises(FleetFileError, match="won't change it"):
        running.save()
    assert path.read_text("utf-8") == 'ships = "Cutlass Black", "Carrack"\n'
    path.write_text('ships = ["Cutlass Black", "Carrack"]\n', "utf-8")
    running.refresh()
    assert running.ships == ["Cutlass Black", "Carrack"]
    running.save()


@pytest.mark.parametrize(
    "text", ['ships = "Cutlass Black"\n', 'ships = ["Cutlass Black"]\nimported = "Carrack"\n']
)
def test_a_fleet_in_the_wrong_shape_is_kept(text: str, tmp_path: Path) -> None:
    """``ships`` that isn't a list was read as no ships, and the next save wrote over it."""
    path = tmp_path / "fleet.toml"
    path.write_text(text, "utf-8")
    fleet = Fleet.load(path)
    assert "won't change it" in fleet.problem
    with pytest.raises(FleetFileError, match="won't change it"):
        fleet.save()
    assert path.read_text("utf-8") == text


def test_broken_memory_and_blueprints_are_kept(tmp_path: Path) -> None:
    notes = tmp_path / "memory.toml"
    notes.write_text(BROKEN, "utf-8")
    store = NoteFile(notes)
    assert store.notes() == []
    assert "memory.toml" in store.problem
    with pytest.raises(MemoryFileError, match=r"memory\.toml"):
        store.add("a new note", datetime.date(2026, 11, 23))
    assert notes.read_text("utf-8") == BROKEN
    owned_path = tmp_path / "blueprints.toml"
    owned_path.write_text(BROKEN, "utf-8")
    owned = OwnedBlueprints(owned_path, lambda: None)
    assert owned.listed() == []
    assert "blueprints.toml" in owned.problem
    with pytest.raises(OSError, match=r"blueprints\.toml"):
        owned.add("FS-9 LMG")
    assert owned_path.read_text("utf-8") == BROKEN


@pytest.mark.parametrize(
    "text",
    ['blueprint = "FS-9 LMG"\n', 'blueprint = ["FS-9 LMG"]\n', 'taken_off = "Arclight Pistol"\n'],
)
def test_blueprints_in_the_wrong_shape_are_kept(text: str, tmp_path: Path) -> None:
    """Read as no blueprints before, so the next one added wrote over the player's list."""
    path = tmp_path / "blueprints.toml"
    path.write_text(text, "utf-8")
    owned = OwnedBlueprints(path, lambda: None)
    assert owned.listed() == []
    assert "blueprints.toml" in owned.problem
    with pytest.raises(OSError, match=r"blueprints\.toml"):
        owned.add("Arclight Pistol")
    assert path.read_text("utf-8") == text


def test_a_broken_history_line_is_skipped_and_the_rest_kept(tmp_path: Path) -> None:
    folder = tmp_path / "history"
    shutil.copytree(FIXTURES / "main" / "history", folder)
    [path] = folder.glob("*.jsonl")
    path.write_text(BROKEN + "\n" + path.read_text("utf-8"), "utf-8")
    store = HistoryFolder(folder)
    assert len(store.entries(path.stem)) == 2
