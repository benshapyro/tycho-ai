"""Starting again after a new game is picked (#295), from the review of #328: a game
that won't start (#329), a quit just after picking one (#330), and the Commands page's
keybinds after a switch (#331). Star Citizen is the only pack with keybinds yet, so a
second game is stood in for."""

# pyright: reportPrivateUsage=false

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

import tycho.desktop.setup
from tycho import app, settings
from tycho.desktop import bridge as bridge_module
from tycho.desktop.bridge import Bridge
from tycho.desktop.host import Host
from tycho.game.keybinds import Keybinds
from tycho.game.store import GameDataError
from tycho.packs import GamePack, built_in
from tycho.settings import Settings

OTHER = "other-game"
"""A second game: not one the settings accept yet, so it's built without checks."""


def _other() -> Settings:
    fields: dict[str, Any] = {**Settings().model_dump(), "game": OTHER}
    return Settings.model_construct(**fields)


@pytest.fixture
def two_games(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Saved settings that can hold the other game, and both games' packs."""
    saved: dict[str, Any] = {"now": _other()}
    names = {"star-citizen": "Star Citizen", OTHER: "Other Game"}

    def pack(game: str = "star-citizen") -> _Named:
        return _Named(names[game])

    def update(changes: dict[str, object], *_: object) -> None:
        if changes.get("game") == "star-citizen":
            saved["now"] = Settings()

    def load(*_: object) -> Settings:
        return saved["now"]

    monkeypatch.setattr(app.settings, "load", load)
    monkeypatch.setattr(app.settings, "update", update)
    monkeypatch.setattr(app, "built_in", pack)
    monkeypatch.setattr(app, "start_update_check", _nothing)
    return saved


def _nothing(*_: object) -> None:
    return None


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def test_a_new_game_that_won_t_start_falls_back_to_the_last_one_and_says_why(
    two_games: dict[str, Any], tmp_path: Path
) -> None:
    starts: list[tuple[str, tuple[str, ...]]] = []

    def start(current: Settings, **options: Any) -> int:
        starts.append((current.game, tuple(options["notices"])))
        if len(starts) == 1:
            return app.RESTART  # Star Citizen ran; the other game was picked
        if str(current.game) == OTHER:
            raise FileNotFoundError("Other Game's folder isn't there")
        return 0

    with pytest.MonkeyPatch.context() as m:
        m.setattr(app, "_start", start)
        code = app._run(Settings(), tmp_path / "log", window=False)
    assert code == 0  # not 1, with the window already closed: Tycho didn't vanish
    assert [game for game, _ in starts] == ["star-citizen", OTHER, "star-citizen"]
    (said,) = starts[-1][1]
    assert "Couldn't start Other Game" in said
    assert "folder isn't there" in said
    assert "back on Star Citizen" in said
    assert two_games["now"].game == "star-citizen"  # saved back


def test_a_game_that_won_t_start_at_all_still_stops_tycho(
    two_games: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only after a switch is there a game to go back to.
    def start(current: Settings, **options: Any) -> int:
        raise GameDataError("no keybinds")

    monkeypatch.setattr(app, "_start", start)
    with pytest.raises(GameDataError):
        app._run(Settings(), tmp_path / "log", window=False)


def test_a_new_game_that_needs_setting_up_opens_setup_and_goes_back_if_it_isn_t_finished(
    two_games: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts: list[tuple[str, tuple[str, ...]]] = []
    answers = [app.RESTART, 0]

    def start(current: Settings, **options: Any) -> int:
        starts.append((current.game, tuple(options["notices"])))
        return answers.pop(0)

    setups: list[str] = []

    def setup(_requests: object) -> str:
        setups.append("setup")
        return "closed"

    monkeypatch.setattr(app, "_start", start)
    monkeypatch.setattr(app, "_Starting", _nothing)
    monkeypatch.setattr(sys, "platform", "win32")

    def needs_setup(current: Settings) -> bool:
        return str(current.game) == OTHER

    monkeypatch.setattr(tycho.desktop.setup, "needs_setup", needs_setup)
    monkeypatch.setattr(app, "first_run", setup)
    assert app._run(Settings(), tmp_path / "log", window=True) == 0
    assert setups == ["setup"]  # the window asked, rather than Tycho failing to start
    assert [game for game, _ in starts] == ["star-citizen", "star-citizen"]
    (said,) = starts[-1][1]
    assert "Couldn't start Other Game (its setup wasn't finished)" in said
    assert two_games["now"].game == "star-citizen"


# --- A quit just after picking a game (#330) --------------------------------------------


def test_quitting_just_after_a_restart_was_asked_for_quits() -> None:
    held = threading.Event()

    async def voice(stop: asyncio.Event) -> None:
        held.set()
        await stop.wait()

    host = Host(voice)
    runner = threading.Thread(target=host.run, daemon=True)
    runner.start()
    assert held.wait(5)
    assert host.restart()
    host.ask("quit")  # before the main thread handled the restart
    runner.join(5)
    assert not runner.is_alive()
    assert not host.restarting
    assert not host.restart()  # and none after it


# --- The Commands page's keybinds after a switch (#331) ---------------------------------


def test_the_commands_page_reads_the_new_game_s_keybinds_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(), path)
    bridge = Bridge(settings_path=path)
    now: dict[str, Settings] = {"settings": Settings()}
    reads: list[str] = []

    def read(current: Settings) -> Any:
        reads.append(current.game)
        return type("Store", (), {"keybinds": Keybinds([])})()

    def load(*_: object) -> Settings:
        return now["settings"]

    def star_citizen(_current: Settings) -> GamePack:
        return built_in()

    def find_install(current: Settings) -> Settings:
        return current

    monkeypatch.setattr(bridge_module.settings, "load", load)
    monkeypatch.setattr(bridge, "_game_pack", star_citizen)
    monkeypatch.setattr("tycho.app.find_install", find_install)
    monkeypatch.setattr("tycho.game.store.KeybindStore.load", read)
    bridge._keybinds()
    bridge._keybinds()
    assert reads == ["star-citizen"]  # kept a while
    now["settings"] = _other()
    bridge._keybinds()
    assert reads == ["star-citizen", OTHER]  # a new game: read again, not the last one's
