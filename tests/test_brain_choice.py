"""Choosing the brain's provider (#112): the setting, its allowlist, its key, the
look-ups it gets, and what ``tycho check`` says. Fakes only: no key is sent anywhere."""

import asyncio
import json
from pathlib import Path

import pytest

from tests.fakes.fake_chat import FakeChat, tool_call
from tests.fakes.fake_key_check import FakeKeyCheck
from tycho import brain_choice, credentials, settings, spoken_settings
from tycho.brain import BrainRequest, RunActions
from tycho.brain.claude import MODEL, ClaudeBrain
from tycho.brain.openrouter import OpenRouterBrain
from tycho.brain.providers import ANTHROPIC_HAIKU, OFFERED, OPENROUTER_HAIKU, choose
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Action, Keybinds, KeyInput
from tycho.knowledge.web import SearchNeedsAnthropic, WebSearch
from tycho.runtime.planning import Questions, plan_request
from tycho.runtime.routing import Picked, from_brain
from tycho.settings import Settings, SettingsError

MATCHER = CommandMatcher.default()
ANTHROPIC = "sk-ant-api03-" + "a" * 40
OPENROUTER = "sk-or-v1-" + "b" * 60


def _store(**keys: str) -> None:
    for name, value in keys.items():
        credentials.set_secret(name, value)


# --- The setting -------------------------------------------------------------------------


def test_claude_direct_is_the_default_so_nothing_changes_for_players() -> None:
    current = Settings()
    assert (current.brain_provider, current.brain_model) == ("anthropic", "Claude Haiku 5.5")
    assert current.brain_seconds == 8.0
    assert brain_choice.chosen(current).model_id == MODEL == ANTHROPIC_HAIKU


def test_haiku_through_openrouter_can_be_picked() -> None:
    choice = brain_choice.chosen(Settings(brain_provider="openrouter"))
    assert (choice.model_id, choice.key) == (OPENROUTER_HAIKU, "openrouter")
    assert choice.describe() == "Claude Haiku 5.5 through OpenRouter"


@pytest.mark.parametrize(
    "change",
    [
        {"brain_provider": "openai"},
        {"brain_model": "openai/gpt-oss-120b"},  # no free-text model ids
        {"brain_model": "anthropic/claude-haiku-5.5"},  # not by its id either
        {"brain_seconds": 2.0},
        {"brain_seconds": 60.0},
    ],
)
def test_only_the_allowlist_and_sensible_times_are_accepted(
    tmp_path: Path, change: dict[str, object]
) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(), path)
    with pytest.raises(SettingsError):
        settings.update(change, path)


def test_a_model_a_provider_doesnt_offer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each provider has its own list: a model that passed on one hasn't on another.
    monkeypatch.setitem(OFFERED, "openrouter", {})  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="OpenRouter offers nothing"):
        Settings(brain_provider="openrouter")
    with pytest.raises(ValueError, match="OpenRouter offers nothing"):
        choose("openrouter", "Claude Haiku 5.5")


def test_every_offered_model_is_a_name_the_setting_accepts() -> None:
    for provider, models in OFFERED.items():
        for model in models:
            assert Settings(brain_provider=provider, brain_model=model).brain_model == model


@pytest.mark.parametrize("name", ["brain_provider", "brain_model", "brain_seconds"])
def test_the_brain_never_changes_by_voice(name: str) -> None:
    # The provider decides who receives what the player says (ADR 5).
    assert settings.rule(name)["voice"] == "no"
    with pytest.raises(spoken_settings.Refused):
        spoken_settings.check_rule(name, "anthropic", "openrouter")


def test_the_setting_is_written_to_the_file_with_its_description(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.update({"brain_provider": "openrouter"}, path)
    text = path.read_text("utf-8")
    assert 'brain_provider = "openrouter"' in text
    assert settings.load(path).brain_provider == "openrouter"


# --- The brain it makes --------------------------------------------------------------------


def _load(current: Settings) -> ClaudeBrain | OpenRouterBrain | None:
    brain = brain_choice.load_brain(MATCHER, current)
    assert brain is None or isinstance(brain, ClaudeBrain | OpenRouterBrain)
    return brain


def _close(brain: ClaudeBrain | OpenRouterBrain) -> None:
    asyncio.run(brain.close())


def test_through_openrouter_the_openrouter_key_runs_haiku() -> None:
    _store(openrouter=OPENROUTER)
    brain = _load(Settings(brain_provider="openrouter"))
    assert isinstance(brain, OpenRouterBrain)
    assert brain._model == OPENROUTER_HAIKU  # pyright: ignore[reportPrivateUsage]
    _close(brain)


def test_an_anthropic_key_alone_doesnt_run_the_openrouter_brain() -> None:
    # The player chose OpenRouter: their words mustn't go to a provider they didn't pick.
    _store(anthropic=ANTHROPIC)
    assert _load(Settings(brain_provider="openrouter")) is None


def test_an_openrouter_key_alone_doesnt_run_claude_direct() -> None:
    _store(openrouter=OPENROUTER)
    assert _load(Settings()) is None


def test_claude_direct_still_runs_on_the_anthropic_key() -> None:
    _store(anthropic=ANTHROPIC)
    brain = _load(Settings())
    assert isinstance(brain, ClaudeBrain)
    _close(brain)


def test_through_openrouter_without_an_anthropic_key_search_says_it_needs_one() -> None:
    _store(openrouter=OPENROUTER)
    brain = _load(Settings(brain_provider="openrouter"))
    assert isinstance(brain, OpenRouterBrain)
    search = brain._lookups["web_search"]  # pyright: ignore[reportPrivateUsage]
    assert isinstance(search, SearchNeedsAnthropic)
    _close(brain)


def test_through_openrouter_search_uses_the_anthropic_key_when_there_is_one() -> None:
    _store(openrouter=OPENROUTER, anthropic=ANTHROPIC)
    brain = _load(Settings(brain_provider="openrouter"))
    assert isinstance(brain, OpenRouterBrain)
    assert isinstance(brain._lookups["web_search"], WebSearch)  # pyright: ignore[reportPrivateUsage]
    _close(brain)


def test_with_web_search_off_there_is_no_search_at_all() -> None:
    _store(openrouter=OPENROUTER)
    brain = _load(Settings(brain_provider="openrouter", web_search=False))
    assert isinstance(brain, OpenRouterBrain)
    assert "web_search" not in brain._lookups  # pyright: ignore[reportPrivateUsage]
    _close(brain)


def test_the_stand_in_search_answers_plainly_and_isnt_an_error() -> None:
    stand_in = SearchNeedsAnthropic()
    result = asyncio.run(stand_in.run({"query": "Star Citizen 4.11 patch"}))
    assert not result.is_error  # an error would make the model try again
    assert "needs an Anthropic key" in json.loads(result.content)["not_available"]
    # The same tool as the real search, so the briefing and tools don't change.
    real = WebSearch.__new__(WebSearch)
    assert (stand_in.name, stand_in.description, stand_in.input_schema) == (
        real.name,
        real.description,
        real.input_schema,
    )


# --- The safety gate on the OpenRouter route --------------------------------------------------


def _action(name: str, key: str, label: str) -> Action:
    return Action(
        name=name, actionmap="m", label=label, inputs=(KeyInput.parse(key) or KeyInput(key),)
    )


def test_a_confirm_grade_pick_through_openrouter_still_asks_first() -> None:
    # The gate is independent of every model (ADR 10): an eject the OpenRouter brain
    # picks is asked about, never pressed straight away.
    keybinds = Keybinds([_action("v_eject", "ralt+y", "Eject")])
    client = FakeChat(tool_call("run_actions", {"actions": ["v_eject"], "say": "Ejecting."}))
    brain = OpenRouterBrain(MATCHER, model=OPENROUTER_HAIKU, client=client)
    decision = asyncio.run(brain.decide(BrainRequest("get me out of here", keybinds)))
    assert decision == RunActions(("v_eject",), "Ejecting.")
    picked = from_brain(decision, MATCHER)
    assert isinstance(picked, Picked)
    assert isinstance(plan_request(picked.commands, keybinds), Questions)


# --- tycho check ------------------------------------------------------------------------------


def _findings(current: Settings, **options: object) -> dict[str, brain_choice.Finding]:
    found = brain_choice.brain_findings(current, **options)  # pyright: ignore[reportArgumentType]
    return {f.what: f for f in found}


def test_check_names_the_provider_and_says_its_key_works() -> None:
    _store(openrouter=OPENROUTER, anthropic=ANTHROPIC)
    check = FakeKeyCheck()
    found = _findings(Settings(brain_provider="openrouter"), check=check)
    assert found["Brain"].level == "ok"
    assert "through OpenRouter" in found["Brain"].detail
    assert "key works" in found["Brain"].detail
    assert check.checked == ["openrouter"]  # the chosen provider's key, nobody else's
    assert "Web search" not in found


def test_check_says_when_the_provider_refuses_the_key() -> None:
    _store(openrouter=OPENROUTER)
    check = FakeKeyCheck("OpenRouter refused that key. Copy it again, or make a new one.")
    found = _findings(Settings(brain_provider="openrouter"), check=check)
    assert found["Brain"].level == "warn"
    assert "refused" in found["Brain"].detail
    assert found["Brain"].fix.endswith("key set openrouter")


def test_check_asks_for_the_chosen_providers_key() -> None:
    _store(anthropic=ANTHROPIC)
    found = _findings(Settings(brain_provider="openrouter"), quick=True)
    assert found["Brain"].level == "warn"
    assert "no OpenRouter key" in found["Brain"].detail
    assert found["Brain"].fix.endswith("key set openrouter")


def test_the_quick_check_asks_nobody() -> None:
    _store(anthropic=ANTHROPIC)
    check = FakeKeyCheck()
    found = _findings(Settings(), quick=True, check=check)
    assert found["Brain"].level == "ok"
    assert "direct from Anthropic" in found["Brain"].detail
    assert check.checked == []


def test_check_says_search_needs_an_anthropic_key_through_openrouter() -> None:
    _store(openrouter=OPENROUTER)
    found = _findings(Settings(brain_provider="openrouter"), quick=True)
    assert found["Web search"].level == "warn"
    assert "needs an Anthropic key" in found["Web search"].detail
    assert "Web search" not in _findings(
        Settings(brain_provider="openrouter", web_search=False), quick=True
    )
