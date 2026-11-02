"""The brain's checks, its briefing, and the Claude adapter against a fake client (ADR 14)."""

import asyncio
import datetime
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.fakes.fake_lookup import FakeLookup
from tycho.brain import (
    BrainError,
    BrainRequest,
    Exchange,
    Reply,
    RunActions,
    RunSequence,
    commands_for,
    parse_tool_call,
)
from tycho.brain.briefing import (
    bound_actions,
    briefed,
    briefing,
    command_phrasings,
    game_facts,
)
from tycho.brain.claude import MAX_CALLS, ClaudeBrain, history
from tycho.brain.lookups import run_lookup
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


def test_the_briefing_tells_claude_how_players_say_each_command() -> None:
    lines = command_phrasings(KEYBINDS, MATCHER)
    gear = [line for line in lines if line.startswith("v_toggle_landing_system |")]
    assert len(gear) == 3  # toggle, down and up all fall back to the toggle here
    assert any("gear down" in line for line in gear)
    assert not any("v_lights" in line for line in lines)  # no key, so not offered
    assert "how it's said" in briefing(KEYBINDS, MATCHER)


def test_actions_nobody_says_are_left_out_unless_a_command_presses_them() -> None:
    assert not briefed("v_pitch_up", "spaceship_movement")  # held steering
    assert not briefed("emote_cs_yes", "player_emotes")
    assert briefed("v_strafe_trim_reset_short", "spaceship_movement")
    assert briefed("v_master_mode_set_scm", "spaceship_movement")
    camera = Action(
        name="view_cycle", actionmap="view_director_mode", label="Cycle", inputs=(KeyInput("f4"),)
    )
    keybinds = Keybinds([camera])
    assert bound_actions(keybinds) == {}
    assert set(bound_actions(keybinds, keep=frozenset({"view_cycle"}))) == {"view_cycle"}


def test_each_request_carries_todays_date_outside_the_cached_briefing() -> None:
    client = _Client([_tool_stop("run_sequence", {"name": "startup", "say": ""})])
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), today=lambda: datetime.date(2026, 11, 1))
    asyncio.run(brain.decide(BrainRequest("start us up", KEYBINDS)))
    (request,) = client.requests
    assert request["messages"][-1]["content"][0] == {"type": "text", "text": "[1 November 2026]"}
    assert request["messages"][-1]["content"][1]["text"] == "start us up"
    assert "[1 November 2026]" not in request["system"][0]["text"]  # per request only


def test_the_briefing_carries_the_current_game_facts_and_no_keys() -> None:
    text = briefing(KEYBINDS, MATCHER)
    facts = game_facts()
    assert facts in text
    assert facts.startswith("Patch: Alpha ")  # dated, so Claude can say "as of"
    assert "Port Olisar is gone" in facts
    # Facts never name keys: Tycho presses the player's own keybinds.
    assert not re.search(r"\b(alt|ctrl|shift)\s*\+|\bkey(bind)?s?\b", facts, re.IGNORECASE)


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


def _tool_stop(name: str, arguments: dict[str, Any], use_id: str = "toolu_1") -> Any:
    block = SimpleNamespace(type="tool_use", id=use_id, name=name, input=arguments)
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
    return asyncio.run(brain.decide(BrainRequest(text, KEYBINDS)))


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


def test_a_bare_action_an_announced_command_uses_counts_as_announced() -> None:
    (lights,) = commands_for(RunActions(("v_lights",), ""), MATCHER)
    assert not lights.announced
    (toggle,) = commands_for(RunActions(("v_power_toggle",), ""), MATCHER)
    assert toggle.id == "v_power_toggle"  # not power_on's own first action, so it runs bare
    assert toggle.announced


def test_warming_up_caches_exactly_what_a_real_request_sends() -> None:
    client = _Client([_tool_stop("run_sequence", {"name": "startup", "say": ""})], _final(""))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client))

    async def scenario() -> None:
        await brain.warm(KEYBINDS)
        await brain.decide(BrainRequest("start us up", KEYBINDS))

    asyncio.run(scenario())
    warm, real = client.requests
    assert warm["max_tokens"] == 0  # no output billed
    for part in ("model", "system", "tools", "tool_choice", "output_config"):
        assert warm[part] == real[part], part


# --- Look-ups: the loop (ADR 15, #54) -------------------------------------------------------


class _Calls:
    """A client that answers each call to Claude with the next scripted (events, final)."""

    def __init__(self, *calls: tuple[list[Any], Any]) -> None:
        self.requests: list[dict[str, Any]] = []
        script = list(calls)

        @asynccontextmanager
        async def stream(**request: Any) -> AsyncGenerator[_Stream]:
            # Copy the messages: the brain appends to the same list between calls.
            self.requests.append({**request, "messages": list(request["messages"])})
            events, final = script.pop(0)
            yield _Stream(events, final)

        self.messages = SimpleNamespace(stream=stream)


def _looks_up(name: str = "ship_lookup", use_id: str = "toolu_1") -> tuple[list[Any], Any]:
    """A call where Claude asks for one look-up and stops to wait for it."""
    stop = _tool_stop(name, {"name": "Andromeda"}, use_id)
    return [stop], SimpleNamespace(
        content=[stop.content_block], stop_reason="tool_use", usage=_final("").usage
    )


def _ask(client: _Calls, *lookups: FakeLookup) -> Any:
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=lookups)
    return asyncio.run(brain.decide(BrainRequest("who makes the Andromeda", KEYBINDS)))


def test_a_look_up_goes_back_to_claude_and_claude_answers() -> None:
    lookup = FakeLookup()
    client = _Calls(_looks_up(), ([], _final("RSI makes it.")))
    assert _ask(client, lookup) == Reply("RSI makes it.")
    assert lookup.asked == [{"name": "Andromeda"}]
    first, second = client.requests
    assert first["tools"] == second["tools"]  # the same tools every call, so the cache holds
    assert first["tool_choice"] == second["tool_choice"]
    assistant, results = second["messages"][-2:]
    assert assistant["role"] == "assistant"
    assert results == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": '{"name": "Constellation Andromeda", "maker": "RSI"}',
                "is_error": False,
            }
        ],
    }


def test_look_ups_are_offered_alongside_the_acts() -> None:
    client = _Calls(([], _final("RSI.")))
    _ask(client, FakeLookup())
    (request,) = client.requests
    assert [t["name"] for t in request["tools"]] == ["run_sequence", "run_actions", "ship_lookup"]
    assert all(t["strict"] for t in request["tools"])


def test_claude_can_act_after_a_look_up_and_the_act_is_still_checked() -> None:
    act = _tool_stop("run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."})
    assert _ask(_Calls(_looks_up(), ([act], None)), FakeLookup()) == RunActions(
        ("v_toggle_landing_system",), "Gear."
    )
    bad = _tool_stop("run_actions", {"actions": ["v_unbound"], "say": ""})
    with pytest.raises(BrainError, match="isn't one of your bound"):
        _ask(_Calls(_looks_up(), ([bad], None)), FakeLookup())


def test_a_broken_look_up_becomes_an_error_result_not_a_crash() -> None:
    client = _Calls(_looks_up(), ([], _final("I couldn't check that.")))
    assert _ask(client, FakeLookup(answer=RuntimeError("down"))) == Reply("I couldn't check that.")
    (result,) = client.requests[1]["messages"][-1]["content"]
    assert result["is_error"]
    assert "failed (RuntimeError)" in result["content"]


@pytest.mark.parametrize(
    ("lookup", "arguments", "problem"),
    [
        (FakeLookup(hang=True), {"name": "x"}, "too long"),
        (None, {"name": "x"}, "no look-up by that name"),
        (FakeLookup(), ["x"], "must be an object"),
    ],
)
def test_run_lookup_never_raises(
    lookup: FakeLookup | None, arguments: object, problem: str
) -> None:
    result = asyncio.run(run_lookup(lookup, arguments, seconds=0.01))
    assert result.is_error
    assert problem in result.content


def test_a_tool_claude_invents_is_refused() -> None:
    with pytest.raises(BrainError, match="unknown tool 'ship_lookup'"):
        _ask(_Calls(_looks_up(), ([], _final("never"))))  # no look-ups offered


def test_looking_things_up_forever_is_a_brain_error() -> None:
    client = _Calls(*[_looks_up(use_id=f"toolu_{n}") for n in range(MAX_CALLS)])
    with pytest.raises(BrainError, match="still looking"):
        _ask(client, FakeLookup())
    assert len(client.requests) == MAX_CALLS


def test_a_look_up_cant_take_the_name_of_an_act() -> None:
    with pytest.raises(ValueError, match="run_actions"):
        ClaudeBrain(MATCHER, client=cast(Any, _Calls()), lookups=[FakeLookup(name="run_actions")])
