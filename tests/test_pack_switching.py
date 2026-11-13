"""Switching game packs in the app (#295, ADR 18): Settings lists each pack with its mode
and why, picking one restarts the voice loop, and the Commands page is the chosen game's."""

import asyncio
import threading
import tomllib
from pathlib import Path

import pytest

from tests.fakes.fake_pack import FakeGameKeybinds, fake_pack
from tycho import app, settings
from tycho.desktop.bridge import Bridge
from tycho.desktop.commands import Commands
from tycho.desktop.host import Host
from tycho.packs import YOU_TURNED_THEM_OFF, as_run, built_in
from tycho.settings import Settings


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "settings.toml"
    settings.save(Settings(), p)
    return p


# --- Settings lists the packs, and picking one restarts the voice loop ---------------------


def test_the_game_setting_lists_each_pack_with_its_mode_and_why(path: Path) -> None:
    bridge = Bridge(settings_path=path)
    rows = {r["name"]: r for g in bridge.settings()["groups"] for r in g["rows"]}  # type: ignore[union-attr]
    assert rows["game"]["packs"] == [
        {"id": "star-citizen", "name": "Star Citizen", "mode": "keys", "why": ""},
        {
            "id": "wow-forever",
            "name": "World of Warcraft: Forever",
            "mode": "advisor",
            "why": built_in("wow-forever").why,
        },
    ]
    assert "packs" not in rows["game_channel"]


def test_a_new_game_restarts_the_voice_loop(path: Path) -> None:
    asked: list[str] = []

    def restart() -> bool:
        asked.append("restart")
        return True

    bridge = Bridge(settings_path=path, restart=restart)
    # Star Citizen is the only pack yet, so "a change" is applied as one would be.
    result = bridge._applied("game", "star-citizen")  # pyright: ignore[reportPrivateUsage]
    assert asked == ["restart"]
    assert result["applies"] == "restarting"


def test_with_nothing_running_a_new_game_applies_at_the_next_start(path: Path) -> None:
    bridge = Bridge(settings_path=path)  # the first run's window: nothing listens yet
    result = bridge._applied("game", "star-citizen")  # pyright: ignore[reportPrivateUsage]
    assert result["applies"] == "restart"


def test_other_settings_never_restart(path: Path) -> None:
    asked: list[str] = []
    bridge = Bridge(settings_path=path, restart=lambda: bool(asked.append("restart")) or True)
    bridge.set("voice_volume", 0.5)
    bridge.set("key_actions", False)
    assert asked == []


def test_a_restart_stops_the_voice_loop_closes_the_window_and_says_why() -> None:
    started, stopped = threading.Event(), threading.Event()
    closed: list[str] = []

    async def voice(stop: asyncio.Event) -> None:
        started.set()
        await stop.wait()
        stopped.set()

    host = Host(voice)
    host.on_quit = lambda: closed.append("window")
    runner = threading.Thread(target=host.run, daemon=True)
    runner.start()
    assert started.wait(5)
    assert host.restart()
    runner.join(5)
    assert not runner.is_alive()
    assert stopped.is_set()  # it stopped as quitting does, letting go of any key
    assert host.restarting
    assert "window" in closed


def test_a_restart_with_nothing_running_does_nothing() -> None:
    async def voice(stop: asyncio.Event) -> None:
        await stop.wait()

    host = Host(voice)
    assert not host.restart()
    assert not host.restarting


def test_tycho_starts_again_with_the_saved_settings_and_its_window_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starts: list[tuple[bool, bool]] = []
    answers = [app.RESTART, 0]

    def start(current: Settings, **options: object) -> int:
        starts.append((current.key_actions, bool(options["reopen"])))
        return answers.pop(0)

    monkeypatch.setattr(app, "_start", start)

    def no_update_check(_current: Settings) -> None:
        pass

    monkeypatch.setattr(app, "start_update_check", no_update_check)
    monkeypatch.setattr(app.settings, "load", lambda: Settings(key_actions=False))
    code = app._run(Settings(), tmp_path / "log", window=False)  # pyright: ignore[reportPrivateUsage]
    assert code == 0
    # The first start as given; the second with the settings as saved, window open.
    assert starts == [(True, False), (False, True)]


# --- The Commands page is the chosen game's ------------------------------------------------


def test_the_commands_page_shows_the_games_own_commands(tmp_path: Path) -> None:
    page = Commands(tmp_path / "commands.toml", lambda: FakeGameKeybinds().keybinds, fake_pack())
    data = page.rows()
    rows: dict[str, dict[str, object]] = {str(r["id"]): r for r in data["rows"]}  # type: ignore[union-attr]
    assert sorted(rows) == ["blow_up", "get_ready", "open_hatch", "raise_shield"]
    assert data["game_name"] == "Fake Game"
    assert data["keys_off"] == ""
    # Fake Game's own confirm list: the hatch asks first (Star Citizen's wouldn't).
    assert rows["open_hatch"]["confirm"] is True
    assert rows["get_ready"]["confirm"] is True
    assert rows["raise_shield"]["confirm"] is False


def test_a_phrase_for_another_games_built_in_command_adds_only_the_phrase(
    tmp_path: Path,
) -> None:
    file = tmp_path / "commands.toml"
    page = Commands(file, lambda: FakeGameKeybinds().keybinds, fake_pack())
    assert page.add_phrase("raise_shield", "brace") == {"ok": True, "applies": "restart"}
    written = tomllib.loads(file.read_text("utf-8"))
    assert written["phrases"] == [{"command": "raise_shield", "say": ["brace"]}]
    rows = {str(r["id"]): r for r in page.rows()["rows"]}  # type: ignore[union-attr]
    assert rows["raise_shield"]["mine"] == ["brace"]


def test_with_key_actions_off_the_commands_page_says_they_press_nothing(path: Path) -> None:
    settings.update({"key_actions": False}, path)
    data = Bridge(settings_path=path).commands()
    assert data["game_name"] == "Star Citizen"
    assert data["keys_off"] == f"Tycho doesn't press keys in Star Citizen. {YOU_TURNED_THEM_OFF}"
    assert data["keys_off"] == as_run(built_in(), key_actions=False).safety.keys_off
