"""The brain's checks, its briefing, and the Claude adapter against a fake client (ADR 14)."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tycho.brain import (
    BrainError,
    Exchange,
    Reply,
    RunActions,
    RunSequence,
    commands_for,
    parse_tool_call,
)
from tycho.brain.briefing import bound_actions, briefing
from tycho.brain.claude import ClaudeBrain, history
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Action, Keybinds, KeyInput

MATCHER = CommandMatcher.default()
ACTIONS = {"v_toggle_landing_system", "v_eject", "v_toggle_qdrive_engagement", "v_lights"}
SEQUENCES = {"startup", "prepare_to_land"}


def _action(name: str, key: str | None, label: str = "") -> Action:
    inputs = () if key is None else (KeyInput.parse(key) or KeyInput(key),)
    return Action(name=name, actionmap="m", label=label or name.upper(), inputs=inputs)


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n", "Landing System (Toggle)"),
        _action("v_eject", "ralt+y", "Eject"),
        _action("v_unbound", None, "Not bound"),
        _action("v_no_label", "k", " "),
    ]
)


# --- Checking what the brain picked -------------------------------------------------------


def test_a_good_tool_call_becomes_a_decision() -> None:
    picked = parse_tool_call(
        "run_actions", {"actions": ["v_toggle_landing_system"], "say": " Gear. "}, ACTIONS, ()
    )
    assert picked == RunActions(("v_toggle_landing_system",), "Gear.")
    sequence = parse_tool_call("run_sequence", {"name": "startup", "say": ""}, (), SEQUENCES)
    assert sequence == RunSequence("startup", "")


@pytest.mark.parametrize(
    ("name", "arguments", "problem"),
    [
        ("run_actions", {"actions": ["v_warp_drive"], "say": ""}, "isn't one of your bound"),
        ("run_actions", {"actions": [], "say": ""}, "no actions"),
        ("run_actions", {"actions": "v_eject", "say": ""}, "no actions"),
        ("run_actions", {"actions": ["v_lights"] * 9, "say": ""}, "too many"),
        ("run_sequence", {"name": "self_destruct_sequence", "say": ""}, "isn't one of your"),
        ("press_everything", {}, "unknown tool"),
        ("run_actions", "not an object", "aren't an object"),
    ],
)
def test_an_invented_or_odd_tool_call_is_dropped(
    name: str, arguments: object, problem: str
) -> None:
    with pytest.raises(BrainError, match=problem):
        parse_tool_call(name, arguments, ACTIONS, SEQUENCES)


def test_a_long_say_line_is_cut() -> None:
    picked = parse_tool_call(
        "run_actions", {"actions": ["v_lights"], "say": "x" * 500}, ACTIONS, ()
    )
    assert isinstance(picked, RunActions)
    assert len(picked.say) == 200


# --- Turning picks into commands -----------------------------------------------------------


def test_an_action_runs_as_the_command_that_owns_it() -> None:
    (gear,) = commands_for(RunActions(("v_toggle_landing_system",)), MATCHER)
    assert gear.id == "landing_gear"


def test_quantum_picked_by_the_brain_gets_its_setup_and_asks_first() -> None:
    (engage,) = commands_for(RunActions(("v_toggle_qdrive_engagement",)), MATCHER)
    assert engage.id == "quantum_engage"
    assert engage.setup == ("v_master_mode_set_nav", "v_set_quantum_mode")
    assert engage.confirm
    built_in = MATCHER.get("quantum_engage")
    assert built_in is not None
    assert not built_in.confirm  # the fast path's stays instant


def test_an_action_no_command_owns_runs_bare() -> None:
    (bare,) = commands_for(RunActions(("v_some_new_action",)), MATCHER)
    assert (bare.id, bare.actions, bare.setup, bare.accepted_risks) == (
        "v_some_new_action",
        ("v_some_new_action",),
        (),
        (),
    )


def test_a_sequence_with_travel_asks_when_the_brain_picked_it(tmp_path: Any) -> None:
    file = tmp_path / "commands.toml"
    file.write_text(
        '[[sequence]]\nid = "depart"\nsay = ["away we go"]\n'
        'steps = ["flight_ready", "quantum_engage"]\n',
        "utf-8",
    )
    matcher = CommandMatcher.default(file)
    (depart,) = commands_for(RunSequence("depart"), matcher)
    assert [s.command.confirm for s in depart.steps] == [False, True]


# --- The briefing ----------------------------------------------------------------------------


def test_the_briefing_lists_only_actions_tycho_can_press() -> None:
    actions = bound_actions(KEYBINDS)
    assert set(actions) == {"v_toggle_landing_system", "v_eject"}
    assert actions["v_eject"] == "v_eject | Eject | ralt+y"


def test_the_briefing_is_the_same_every_time_so_the_cache_holds() -> None:
    first = briefing(KEYBINDS, MATCHER)
    assert first == briefing(Keybinds(list(reversed(list(KEYBINDS)))), MATCHER)
    assert "startup | flight_ready, then request_departure" in first


def test_history_puts_earlier_exchanges_before_the_request() -> None:
    messages = history([Exchange("gear", "did landing_gear")], "and the lights")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "and the lights"


# --- The Claude adapter, against a fake client -------------------------------------------------


class _Stream:
    def __init__(self, events: list[Any], final: Any) -> None:
        self._events = events
        self._final = final

    def __aiter__(self) -> AsyncIterator[Any]:
        async def events() -> AsyncIterator[Any]:
            for event in self._events:
                yield event

        return events()

    async def get_final_message(self) -> Any:
        return self._final


class _Client:
    def __init__(self, events: list[Any], final: Any = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.closed = False

        @asynccontextmanager
        async def stream(**request: Any) -> AsyncGenerator[_Stream]:
            self.requests.append(request)
            try:
                yield _Stream(events, final)
            finally:
                self.closed = True

        async def create(**request: Any) -> Any:
            self.requests.append(request)
            return final

        self.messages = SimpleNamespace(stream=stream, create=create)


def _tool_stop(name: str, arguments: dict[str, Any]) -> Any:
    block = SimpleNamespace(type="tool_use", name=name, input=arguments)
    return SimpleNamespace(type="content_block_stop", content_block=block)


def _final(text: str, stop_reason: str = "end_turn") -> Any:
    usage = SimpleNamespace(
        input_tokens=20,
        output_tokens=8,
        cache_read_input_tokens=3000,
        cache_creation_input_tokens=0,
    )
    content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


def _decide(client: _Client, text: str = "gear please") -> Any:
    brain = ClaudeBrain(MATCHER, client=cast(Any, client))
    return asyncio.run(brain.decide(text, [], KEYBINDS))


def test_claude_acts_as_soon_as_the_tool_call_is_complete() -> None:
    thinking = SimpleNamespace(
        type="content_block_stop", content_block=SimpleNamespace(type="thinking")
    )
    after = _tool_stop("run_actions", {"actions": ["v_eject"], "say": "never seen"})
    client = _Client(
        [
            thinking,
            _tool_stop("run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."}),
            after,
        ]
    )
    assert _decide(client) == RunActions(("v_toggle_landing_system",), "Gear.")
    assert client.closed  # the stream was left at the first tool call


def test_claude_is_asked_the_way_adr_14_says() -> None:
    client = _Client([_tool_stop("run_sequence", {"name": "startup", "say": ""})])
    _decide(client)
    request = client.requests[0]
    assert request["model"] == "claude-haiku-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert request["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert {t["name"] for t in request["tools"]} == {"run_actions", "run_sequence"}
    assert all(t["strict"] for t in request["tools"])


def test_claude_can_only_pick_actions_the_player_has_bound() -> None:
    client = _Client([_tool_stop("run_actions", {"actions": ["v_unbound"], "say": ""})])
    with pytest.raises(BrainError, match="isn't one of your bound"):
        _decide(client)


def test_a_text_answer_is_a_reply() -> None:
    assert _decide(_Client([], _final("The Prospector."))) == Reply("The Prospector.")


@pytest.mark.parametrize(
    ("final", "problem"),
    [(_final("", "refusal"), "declined"), (_final("", "max_tokens"), "no answer")],
)
def test_no_usable_answer_is_a_brain_error(final: Any, problem: str) -> None:
    with pytest.raises(BrainError, match=problem):
        _decide(_Client([], final))


def test_warming_up_caches_exactly_what_a_real_request_sends() -> None:
    client = _Client([_tool_stop("run_sequence", {"name": "startup", "say": ""})], _final(""))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client))

    async def scenario() -> None:
        await brain.warm(KEYBINDS)
        await brain.decide("start us up", [], KEYBINDS)

    asyncio.run(scenario())
    warm, real = client.requests
    assert warm["max_tokens"] == 0  # no output billed
    for part in ("model", "system", "tools", "tool_choice", "output_config"):
        assert warm[part] == real[part], part
