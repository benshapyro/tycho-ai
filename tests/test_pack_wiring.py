"""How a game pack is wired into a running Tycho (#323, from the review of #322): the
look-ups and web sites the brain gets, and the session's safety lists and game state.
Star Citizen behaves the same today; these only show with a second real game."""

# pyright: reportPrivateUsage=false

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.fakes.fake_audio import FakeAudioSource
from tests.fakes.fake_keys import FakeFocus, FakeKeySender
from tests.fakes.fake_pack import FOLDER, REGISTRY, FakeGameKeybinds, fake_pack
from tests.fakes.fake_runtime import FakeFeedback, FakeTranscriber
from tycho import app, brain_choice, credentials
from tycho.brain.claude import ClaudeBrain
from tycho.commands import CommandMatcher
from tycho.knowledge.web import WebSearch
from tycho.packs import GamePack, built_in, load_pack
from tycho.ptt import InputSource
from tycho.ptt.binding import Binding
from tycho.settings import Settings

ANTHROPIC = "sk-ant-api03-" + "a" * 40


def _brain(pack: GamePack) -> ClaudeBrain:
    credentials.set_secret("anthropic", ANTHROPIC)
    brain = brain_choice.load_brain(CommandMatcher.default(pack=pack), Settings(), pack=pack)
    assert isinstance(brain, ClaudeBrain)
    return brain


def _offered(brain: ClaudeBrain) -> set[str]:
    offered = set(brain._lookups)
    asyncio.run(brain.close())
    return offered


def _with_web(tmp_path: Path) -> GamePack:
    """Fake Game, naming the web look-up too."""
    folder = tmp_path / "fake-game"
    shutil.copytree(cast(Path, FOLDER), folder)
    toml = folder / "pack.toml"
    toml.write_text(
        toml.read_text("utf-8").replace('lookups = ["settings"]', 'lookups = ["settings", "web"]'),
        "utf-8",
    )
    return load_pack(folder, built_in=False, registry=REGISTRY)


def test_the_brain_gets_only_the_look_ups_the_pack_names_and_no_web_search_without_it() -> None:
    assert _offered(_brain(fake_pack())) == {"settings"}
    star_citizen = _offered(_brain(built_in()))
    assert {"settings", "web_search", "update_fleet"} <= star_citizen


def test_the_web_search_reads_only_the_pack_s_sites(tmp_path: Path) -> None:
    pack = _with_web(tmp_path)
    brain = _brain(pack)
    search = brain._lookups["web_search"]
    assert isinstance(search, WebSearch)
    assert search._sites == pack.web_sites == ("fakegame.example",)
    asyncio.run(brain.close())

    sent: list[dict[str, Any]] = []

    async def create(**request: Any) -> Any:
        sent.append(request)
        return SimpleNamespace(content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    asyncio.run(WebSearch(cast(Any, client), sites=pack.web_sites).run({"query": "patch"}))
    (request,) = sent
    assert request["tools"][0]["allowed_domains"] == ["fakegame.example"]


def _session(pack: GamePack, store: object | None, monkeypatch: pytest.MonkeyPatch) -> Any:
    def no_sources(binding: Binding | None) -> list[InputSource]:
        return []  # no Windows input here

    monkeypatch.setattr(app, "input_sources", no_sources)
    return app.build_session(
        Settings(),
        Binding.parse("mouse:0x06"),
        cast(Any, store),
        FakeTranscriber(),
        audio=FakeAudioSource(16_000),
        sender=FakeKeySender(),
        focus=FakeFocus(True),
        feedback=FakeFeedback(),
        pack=pack,
    )


def test_the_session_gets_the_pack_s_safety_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    pack = fake_pack()
    store = SimpleNamespace(keybinds=FakeGameKeybinds().keybinds)
    session = _session(pack, store, monkeypatch)
    assert session._safety_lists is pack.safety
    assert session._safety_lists != built_in().safety


def test_game_log_is_read_only_for_a_pack_with_a_game_state_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SimpleNamespace(
        keybinds=FakeGameKeybinds().keybinds,
        install=SimpleNamespace(game_log=tmp_path / "Game.log"),
    )
    pack = fake_pack()
    assert pack.parts.game_state is None
    assert _session(pack, store, monkeypatch)._game_state is None

    star_citizen = built_in()
    assert star_citizen.parts.game_state is not None
    session = _session(star_citizen, store, monkeypatch)
    assert type(session._game_state).__name__ == "GameLog"
