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
    unbound_commands,
)
from tycho.brain.claude import ClaudeBrain, history
from tycho.brain.lookups import LOOKUP_SECONDS, run_lookup
from tycho.brain.rounds import (
    CUT_SHORT,
    LAST_CALL,
    LAST_PICK,
    MAX_CALLS,
    NO_PRESS_AFTER_SEARCH,
    spoken,
)
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


def test_no_actions_with_a_line_to_say_is_a_reply() -> None:
    """The #192 eval after #276: for "good fight everyone, o7" Haiku called run_actions
    with no actions and "o7", and the player got an error instead of the line."""
    picked = parse_tool_call("run_actions", {"actions": [], "say": " o7. "}, ACTIONS, ())
    assert picked == Reply("o7.")


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


def test_a_fallback_action_keeps_the_players_confirm(tmp_path: Any) -> None:
    # The briefing offers the action that has a key; here that's the fallback, so the
    # brain names it, not the command's first action.
    file = tmp_path / "commands.toml"
    file.write_text(
        '[[command]]\nid = "careful"\nsay = ["lock it carefully"]\nconfirm = true\n'
        'requires = ["quantum_route_set"]\n'
        'actions = ["v_not_bound_anywhere", "v_lock_rotation"]\n',
        "utf-8",
    )
    (picked,) = commands_for(RunActions(("v_lock_rotation",)), CommandMatcher.default(file))
    assert picked.actions == ("v_lock_rotation",)
    assert picked.confirm
    assert picked.requires == ("quantum_route_set",)


def test_a_players_confirm_holds_on_an_action_a_built_in_starts_with(tmp_path: Any) -> None:
    file = tmp_path / "commands.toml"
    file.write_text(
        '[[command]]\nid = "my_gear"\nsay = ["my wheels"]\nconfirm = true\n'
        'actions = ["v_toggle_landing_system"]\n',
        "utf-8",
    )
    (gear,) = commands_for(RunActions(("v_toggle_landing_system",)), CommandMatcher.default(file))
    assert gear.id == "landing_gear"  # still the built-in command, with its setup and waits
    assert gear.confirm


def test_picks_nobody_marked_stay_as_they_were() -> None:
    (gear,) = commands_for(RunActions(("v_toggle_landing_system",)), MATCHER)
    assert not gear.confirm
    assert gear.requires == ()


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
    assert actions["v_eject"] == "v_eject | Eject"  # what it does, not its key


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


def test_the_briefing_names_commands_with_no_key_and_what_to_bind() -> None:
    # Without a lock key, every model pressed Open/Close Doors for "lock the doors" (#28).
    lines = unbound_commands(KEYBINDS, MATCHER)
    lock = next(line for line in lines if line.startswith("doors_lock |"))
    assert lock == "doors_lock | lock the doors; lock doors | v_lock_all_doors"
    assert not any(line.startswith("landing_gear") for line in lines)  # has a key
    labelled = Keybinds([*KEYBINDS, _action("v_lock_all_doors", None, "Lock All Doors")])
    assert "doors_lock | lock the doors; lock doors | Lock All Doors" in unbound_commands(
        labelled, MATCHER
    )
    text = briefing(KEYBINDS, MATCHER)
    assert "commands with no key" in text
    assert "Never press another action in its place" in text


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
    assert request["tool_choice"] == {"type": "auto"}  # several tools a reply (#149, #156)
    assert request["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert {t["name"] for t in request["tools"]} == {"run_actions", "run_sequence"}
    assert all(t["strict"] for t in request["tools"])


def _picks(actions: list[str], use_id: str = "toolu_1") -> tuple[list[Any], Any]:
    """A call where Claude picks run_actions (the stream runs on if it can't be used)."""
    stop = _tool_stop("run_actions", {"actions": actions, "say": "Done."}, use_id)
    return [stop], SimpleNamespace(
        content=[stop.content_block], stop_reason="tool_use", usage=_final("").usage
    )


def test_an_unbound_action_is_never_pressed_and_claude_picks_again() -> None:
    """Eval (#164): "pop some noise" named v_noise_launch, which isn't bound, and the
    request ended in the error sound. Claude hears the closest bound names instead."""
    client = _Calls(
        _picks(["v_toggle_landing_sys"]), _picks(["v_toggle_landing_system"], "toolu_2")
    )
    assert _ask(client) == RunActions(("v_toggle_landing_system",), "Done.")
    (result,) = client.requests[1]["messages"][-1]["content"]
    assert result["is_error"]
    assert "Nothing was pressed" in result["content"]
    assert "did you mean v_toggle_landing_system" in result["content"]


def test_an_action_with_nothing_close_bound_is_said_not_pressed() -> None:
    client = _Calls(_picks(["v_xyzzy_qqq"]), ([], _final("There's no key for that.")))
    assert _ask(client) == Reply("There's no key for that.")
    (result,) = client.requests[1]["messages"][-1]["content"]
    assert "nothing close is bound" in result["content"]


def test_claude_can_only_pick_actions_the_player_has_bound() -> None:
    client = _Calls(*[_picks(["v_unbound"], f"toolu_{n}") for n in range(MAX_CALLS)])
    with pytest.raises(BrainError, match="isn't one of your bound"):
        _ask(client)  # never pressed, however often it asks; and the log says why (#167)


def test_a_text_answer_is_a_reply() -> None:
    assert _decide(_Client([], _final("The Prospector."))) == Reply("The Prospector.")


@pytest.mark.parametrize(
    ("final", "problem"),
    [(_final("", "refusal"), "declined"), (_final("", "end_turn"), "no answer")],
)
def test_no_usable_answer_is_a_brain_error(final: Any, problem: str) -> None:
    with pytest.raises(BrainError, match=problem):
        _decide(_Client([], final))


def test_a_reply_that_ran_out_of_room_says_so_instead_of_failing() -> None:
    # #266: nothing to say at the output limit was the error sound.
    assert _decide(_Client([], _final("", "max_tokens"))) == Reply(CUT_SHORT)


def _card(**fields: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "say": "Eight steps on the Tile.",
        "kind": "steps",
        "label": "",
        "title": "Salvage",
        "source": "",
        "rows": [{"text": f"Step {n}", "detail": "", "value": "", "dim": False} for n in range(8)],
        "hero_label": "",
        "hero_value": "",
        "text": "",
    }
    return card | fields


def test_a_whole_card_is_shown() -> None:
    brain = ClaudeBrain(
        MATCHER, client=cast(Any, _Client([_tool_stop("show_card", _card())])), cards=lambda: True
    )
    decided = asyncio.run(brain.decide(BrainRequest("teach me how to salvage", KEYBINDS)))
    assert isinstance(decided, Reply)
    assert decided.text == "Eight steps on the Tile."
    assert decided.card is not None
    assert len(decided.card.rows) == 8


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        # #266: a long card cut off mid-way, read by the SDK as far as it got.
        (
            "show_card",
            {
                k: v
                for k, v in _card(rows=_card()["rows"][:3]).items()
                if k in ("say", "kind", "label", "title", "source", "rows")
            },
        ),
        ("show_card", {"say": "Eight steps on the Tile."}),
        # A cut run_actions must never press the actions it got to.
        ("run_actions", {"actions": ["v_toggle_landing_system"]}),
    ],
)
def test_an_act_cut_off_at_the_output_limit_is_not_used(name: str, arguments: Any) -> None:
    stop = _tool_stop(name, arguments)
    brain = ClaudeBrain(MATCHER, client=cast(Any, _Client([stop])), cards=lambda: True)
    decided = asyncio.run(brain.decide(BrainRequest("teach me how to salvage", KEYBINDS)))
    assert decided == Reply(CUT_SHORT)


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
    client = _Calls(_looks_up(), _picks(["v_unbound"], "toolu_2"), ([], _final("No key.")))
    assert _ask(client, FakeLookup()) == Reply("No key.")  # the act was checked: not pressed


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


def test_before_the_last_call_claude_is_told_to_answer_now() -> None:
    """Play session: three look-ups for gold, then the error sound. Before the last
    call Claude is told to answer with what it found; it keeps every tool, since
    taking them away made it write show_card out as text, which was read aloud."""
    calls = [_looks_up(use_id=f"toolu_{n}") for n in range(MAX_CALLS - 1)]
    client = _Calls(*calls, ([], _final("Gold sells best at Area 18.")))
    decision = _ask(client, FakeLookup())
    assert getattr(decision, "text", "") == "Gold sells best at Area 18."
    last = client.requests[-1]
    assert last["tool_choice"]["type"] == "auto"
    assert {"type": "text", "text": LAST_CALL} in last["messages"][-1]["content"]
    assert all(
        {"type": "text", "text": LAST_CALL} not in m["content"]
        for r in client.requests[:-1]
        for m in r["messages"]
        if isinstance(m["content"], list)
    )


def test_a_tool_call_written_as_text_is_never_spoken() -> None:
    client = _Calls(([], _final('show_card({"say": "Buy at Rod\'s", "kind": "route"})')))
    with pytest.raises(BrainError, match="tool call as text"):
        _ask(client)


def test_settings_and_the_fleet_dont_say_checking() -> None:
    """Review of #145: "turn your voice down" got "Checking." before "Done."."""
    instant = FakeLookup("settings", '{"done": true}', instant=True)
    looked: list[str] = []
    client = _Calls(_looks_up("settings"), ([], _final("Done.")))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=(instant,))
    request = BrainRequest("voice down a bit", KEYBINDS, looking=lambda: looked.append(""))
    assert asyncio.run(brain.decide(request)) == Reply("Done.")
    assert looked == []


def test_the_real_settings_and_fleet_tools_are_instant() -> None:
    from tycho.knowledge.fleet import FleetUpdate
    from tycho.spoken_settings import SettingsTool

    assert SettingsTool.instant
    assert FleetUpdate.instant


def test_each_look_up_is_told_to_the_session() -> None:
    """Review of #166: only the first moved the deadline, so a second look-up 4.6 s in
    still ran out of time. The brain says "Checking." once (#212), and tells the
    session of each look-up so each moves the deadline."""
    looked: list[str] = []
    calls = [_looks_up(use_id=f"toolu_{n}") for n in range(2)]
    client = _Calls(*calls, ([], _final("Area 18.")))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=(FakeLookup(),))
    request = BrainRequest("where do I sell gold", KEYBINDS, looking=lambda: looked.append(""))
    assert asyncio.run(brain.decide(request)) == Reply("Area 18.")
    assert len(looked) == 2


def test_a_look_up_cant_take_the_name_of_an_act() -> None:
    with pytest.raises(ValueError, match="run_actions"):
        ClaudeBrain(MATCHER, client=cast(Any, _Calls()), lookups=[FakeLookup(name="run_actions")])


def test_warming_up_also_loads_the_look_ups_data() -> None:
    lookup = FakeLookup()
    brain = ClaudeBrain(MATCHER, client=cast(Any, _Client([], _final(""))), lookups=[lookup])
    asyncio.run(brain.warm(KEYBINDS))
    assert lookup.prepared


def test_warming_again_after_a_rebind_writes_the_cache_but_loads_nothing_twice() -> None:
    """#60: the briefing lists the keybinds, so a rebind needs the cache warmed again."""
    lookup = FakeLookup()
    client = _Client([], _final(""))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=[lookup])

    async def scenario() -> None:
        await brain.warm(KEYBINDS)
        lookup.prepared = False
        await brain.warm(KEYBINDS)

    asyncio.run(scenario())
    assert len(client.requests) == 2
    assert not lookup.prepared


def test_a_rebind_during_the_first_warm_up_still_loads_the_look_ups() -> None:
    """Review of #207: cancelling the first warm-up for new keybinds mustn't leave the
    ship data "still loading" for the whole session."""
    release = asyncio.Event()

    class SlowLookup(FakeLookup):
        async def prepare(self) -> None:
            await release.wait()
            self.prepared = True

    lookup = SlowLookup()
    brain = ClaudeBrain(MATCHER, client=cast(Any, _Client([], _final(""))), lookups=[lookup])

    async def scenario() -> None:
        first = asyncio.create_task(brain.warm(KEYBINDS))
        await asyncio.sleep(0)
        first.cancel()  # the keybinds changed
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(brain.warm(KEYBINDS))
        release.set()
        await second

    asyncio.run(scenario())
    assert lookup.prepared


def test_closing_the_brain_closes_its_own_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """#60: the connection pool was never closed."""
    brain = ClaudeBrain(MATCHER, api_key="test")
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    monkeypatch.setattr(brain._client, "close", close)  # pyright: ignore[reportPrivateUsage]
    asyncio.run(brain.close())
    assert closed == [True]
    lent = _Client([], _final(""))
    asyncio.run(ClaudeBrain(MATCHER, client=cast(Any, lent)).close())  # a lent one is left open


def test_reference_text_goes_at_the_end_of_the_cached_briefing() -> None:
    client = _Client([], _final("RSI."))
    brain = ClaudeBrain(
        MATCHER, client=cast(Any, client), reference="ship | maker\nAndromeda | RSI"
    )
    asyncio.run(brain.decide(BrainRequest("who makes the Andromeda", KEYBINDS)))
    (system,) = client.requests[0]["system"]
    assert system["text"].endswith("\nship | maker\nAndromeda | RSI\n")
    assert system["cache_control"]["type"] == "ephemeral"


# --- Web search (ADR 15) ------------------------------------------------------------------


def _searches(use_id: str = "toolu_1") -> tuple[list[Any], Any]:
    """A call where Claude asks for a web search and stops to wait for it."""
    stop = _tool_stop("web_search", {"query": "Star Citizen next patch"}, use_id)
    return [stop], SimpleNamespace(
        content=[stop.content_block], stop_reason="tool_use", usage=_final("").usage
    )


def _web() -> FakeLookup:
    return FakeLookup("web_search", '{"answer": "4.10.3 is on the PTU."}', from_the_web=True)


def _ask_web(client: _Calls, *lookups: FakeLookup) -> tuple[Any, list[str]]:
    waited: list[str] = []
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=lookups)
    request = BrainRequest("whats in the next patch", KEYBINDS, waiting=lambda: waited.append(""))
    return asyncio.run(brain.decide(request)), waited


def test_a_search_says_its_waiting_once_and_its_answer_is_spoken() -> None:
    client = _Calls(_searches(), ([], _final("4.10.3 is on the PTU now.")))
    decision, waited = _ask_web(client, _web())
    assert decision == Reply("4.10.3 is on the PTU now.")
    assert len(waited) == 1


def test_other_look_ups_dont_say_theyre_waiting() -> None:
    stop = _tool_stop("ship_lookup", {"name": "Andromeda"})
    first = (
        [stop],
        SimpleNamespace(
            content=[stop.content_block], stop_reason="tool_use", usage=_final("").usage
        ),
    )
    _, waited = _ask_web(_Calls(first, ([], _final("RSI."))), FakeLookup())
    assert waited == []


def test_nothing_is_pressed_after_a_web_search() -> None:
    act = _tool_stop("run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."})
    decision, _ = _ask_web(_Calls(_searches(), ([act], None)), _web())
    assert decision == Reply(NO_PRESS_AFTER_SEARCH)


def test_acting_without_a_search_still_presses() -> None:
    act = _tool_stop("run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."})
    decision, _ = _ask_web(_Calls(([act], None)), _web())
    assert decision == RunActions(("v_toggle_landing_system",), "Gear.")


def test_a_search_gets_longer_than_other_look_ups() -> None:
    class Slow(FakeLookup):
        seconds = 0.5

        async def run(self, arguments: Any) -> Any:
            await asyncio.sleep(LOOKUP_SECONDS / 10)
            return await super().run(arguments)

    lookup = Slow("web_search", '{"answer": "ok"}', from_the_web=True)
    client = _Calls(_searches(), ([], _final("Done.")))
    decision, _ = _ask_web(client, lookup)
    assert decision == Reply("Done.")


def test_a_reply_is_one_line_with_no_markdown_to_read_out() -> None:
    assert spoken("Best route is **Quartz**,\n to `Stanton Gateway`. # done") == (
        "Best route is Quartz, to Stanton Gateway. done"
    )


def test_claude_sees_a_few_phrases_per_command_the_short_ones_first() -> None:
    # The fast path matches every phrase; Claude needs only enough to recognise the
    # command said another way. Fewer tokens on every request.
    lines = command_phrasings(KEYBINDS, MATCHER)
    down = next(line for line in lines if "lower the landing gear" in line)
    assert down == "v_toggle_landing_system | lower the landing gear; gear down; drop the gear"
    assert all(line.count(";") <= 2 for line in lines)


def test_claude_is_told_to_name_what_was_sent_not_a_result() -> None:
    # The game ignores some keys in the wrong state, and Tycho can't see it (#29).
    text = briefing(KEYBINDS, MATCHER)
    assert "Name what was sent, never a result" in text
    assert '"Max power to engines", not "Engines are at' in text


# --- The Tile's cards (#2) --------------------------------------------------------

_CARD = {
    "say": "Best is Everus Harbor at 3,048. Two more on the Tile.",
    "kind": "list",
    "label": "Sell · Laranite",
    "title": "Best places to sell",
    "source": "UEX",
    "rows": [
        {"text": "Everus Harbor", "detail": "Hurston", "value": "3,048", "dim": False},
        {"text": "Area18 TDD", "detail": "ArcCorp", "value": "3,012", "dim": False},
    ],
    "hero_label": "",
    "hero_value": "",
    "text": "",
}


def test_a_card_is_a_reply_with_the_details_to_read() -> None:
    decision = parse_tool_call("show_card", _CARD, ACTIONS, SEQUENCES)
    assert isinstance(decision, Reply)
    assert decision.text == _CARD["say"]
    assert decision.card is not None
    assert decision.card.rows[0].value == "3,048"


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"kind": "confirm"}, "isn't a card"),
        ({"say": ""}, "nothing to say"),
        ({"rows": []}, "nothing on it"),
    ],
)
def test_a_card_claude_cant_show_is_a_brain_error(change: dict[str, Any], problem: str) -> None:
    with pytest.raises(BrainError, match=problem):
        parse_tool_call("show_card", _CARD | change, ACTIONS, SEQUENCES)


def _offered(tile: bool) -> list[str]:
    client = _Client([_tool_stop("show_card", _CARD)] if tile else [], _final("Fine."))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), cards=lambda: tile)
    asyncio.run(brain.decide(BrainRequest("where to sell laranite", KEYBINDS)))
    return [t["name"] for t in client.requests[0]["tools"]]


def test_claude_may_show_a_card_only_while_the_tile_is_on() -> None:
    assert "show_card" not in _offered(tile=False)
    assert _offered(tile=True)[:3] == ["run_sequence", "run_actions", "show_card"]


def test_turning_the_tile_on_or_off_takes_effect_at_the_next_request() -> None:
    tile = [False]
    client = _Calls(([], _final("Fine.")), ([], _final("Fine.")))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), cards=lambda: tile[0])
    asyncio.run(brain.decide(BrainRequest("hello", KEYBINDS)))
    tile[0] = True
    asyncio.run(brain.decide(BrainRequest("hello", KEYBINDS)))
    names = [[t["name"] for t in r["tools"]] for r in client.requests]
    assert "show_card" not in names[0]
    assert "show_card" in names[1]


def test_a_card_after_a_web_search_is_shown_since_nothing_is_pressed() -> None:
    card = _CARD | {"kind": "headlines", "say": "Three things stand out."}
    client = _Calls(_searches(), ([_tool_stop("show_card", card, "toolu_2")], None))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=[_web()], cards=lambda: True)
    decision = asyncio.run(brain.decide(BrainRequest("whats in the next patch", KEYBINDS)))
    assert isinstance(decision, Reply)
    assert decision.card is not None
    assert decision.card.kind == "headlines"


def test_a_look_up_cant_take_the_cards_name() -> None:
    with pytest.raises(ValueError, match="show_card"):
        ClaudeBrain(MATCHER, client=cast(Any, _Client([])), lookups=[FakeLookup("show_card")])


def test_the_briefing_says_when_the_eye_beats_the_ear() -> None:
    text = briefing(KEYBINDS, MATCHER)
    assert "show_card" in text
    assert "Two more on the Tile" in text
    assert "Never a card for a one-line answer" in " ".join(text.split())


def test_the_briefing_says_what_not_to_guess() -> None:
    """The #192 eval (#271): most models pressed for vague or drastic requests, squad
    callouts (shields, targeting), "jump to Hurston" (quantum to wherever was set) and
    "fire everything" (weapon power). The briefing's examples are other wordings than
    the eval's cases, so the eval still measures what a model makes of new ones."""
    text = " ".join(briefing(KEYBINDS, MATCHER).split())
    assert "Never guess a sequence, a boost or the throttle" in text
    assert "call no tool, not even for shields or targeting" in text
    assert 'reply with exactly "Copy." and nothing else' in text
    assert "Chat to you, such as thanks, still gets a short friendly reply" in text
    assert 'never "the player", and never explain why you replied' in text
    assert "A request for something you can press is still for you" in text
    assert "the destination is set in the star map" in text
    assert "never engage quantum for a named place" in text
    assert "Tycho doesn't fire them" in text
    cases = ("the usual", "hurry up", "watch your six", "jump to Hurston", "fire everything")
    for case in (*cases, "check behind us", "third person", "good fight everyone, o7"):
        assert f'"{case}"' not in text


def _reply(*stops: Any) -> tuple[list[Any], Any]:
    """One reply in which Claude calls several tools, in this order."""
    return list(stops), SimpleNamespace(
        content=[s.content_block for s in stops], stop_reason="tool_use", usage=_final("").usage
    )


def test_a_fleet_note_with_an_act_is_kept_and_the_act_pressed() -> None:
    """Play session (#156): "can you turn off power, I'm in my Pitbull" pressed power
    off, and the Pitbull was never noted."""
    fleet = FakeLookup("update_fleet", '{"flying": "Pitbull"}', instant=True)
    noted = _tool_stop("update_fleet", {"flying": "Pitbull"}, "toolu_1")
    act = _tool_stop(
        "run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."}, "toolu_2"
    )
    assert _ask(_Calls(_reply(noted, act)), fleet) == RunActions(
        ("v_toggle_landing_system",), "Gear."
    )
    assert fleet.asked == [{"flying": "Pitbull"}]


def test_look_ups_in_one_reply_run_together() -> None:
    ships = FakeLookup("ship_lookup", '{"name": "Cutlass Black", "cargo_scu": 46}')
    routes = FakeLookup("trade_routes", '{"routes": []}')
    both = _reply(
        _tool_stop("ship_lookup", {"name": "Cutlass"}, "toolu_1"),
        _tool_stop("trade_routes", {"start": "Lorville"}, "toolu_2"),
    )
    client = _Calls(both, ([], _final("Haul laranite.")))
    assert _ask(client, ships, routes) == Reply("Haul laranite.")
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_1", "toolu_2"]  # one call, not two


def test_a_tool_call_written_mid_reply_isnt_spoken() -> None:
    """#155: only a reply that began with one was caught."""
    client = _Calls(([], _final('Here you go. show_card({"say": "x", "kind": "list"})')))
    assert _ask(client) == Reply("Here you go.")


def test_after_a_refused_act_a_later_one_in_the_reply_isnt_pressed() -> None:
    """#176: the first act was refused, and a second in the same reply was pressed."""
    bad = _tool_stop("run_actions", {"actions": ["v_unbound"], "say": ""}, "toolu_1")
    good = _tool_stop(
        "run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."}, "toolu_2"
    )
    client = _Calls(_reply(bad, good), ([], _final("Which one did you mean?")))
    assert _ask(client) == Reply("Which one did you mean?")
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_1", "toolu_2"]
    assert "earlier action in this reply was refused" in results[1]["content"]


def test_a_refused_act_isnt_told_to_answer_now() -> None:
    """#167: LAST_CALL beside "pick again" told Claude two things at once."""
    calls = [_looks_up(use_id="toolu_0"), _picks(["v_unbound"], "toolu_1")]
    client = _Calls(*calls, _picks(["v_toggle_landing_system"], "toolu_2"))
    assert _ask(client, FakeLookup()) == RunActions(("v_toggle_landing_system",), "Done.")
    last: list[dict[str, Any]] = client.requests[2]["messages"][-1]["content"]
    assert {"type": "text", "text": LAST_CALL} not in last
    assert {"type": "text", "text": LAST_PICK} in last  # still told it's the last call


def test_no_checking_line_for_a_look_up_an_act_cuts_short() -> None:
    """#176: "Checking." was said for a look-up the act then cancelled. The deadline
    still moved, which is harmless: the press ends the request (#178)."""
    told: list[str] = []
    lookup = _tool_stop("ship_lookup", {"name": "Cutlass"}, "toolu_1")
    act = _tool_stop(
        "run_actions", {"actions": ["v_toggle_landing_system"], "say": "Gear."}, "toolu_2"
    )
    brain = ClaudeBrain(
        MATCHER, client=cast(Any, _Calls(_reply(lookup, act))), lookups=(FakeLookup(),)
    )
    request = BrainRequest(
        "gear",
        KEYBINDS,
        looking=lambda: told.append("looking"),
        checking=lambda: told.append("checking"),
    )
    assert isinstance(asyncio.run(brain.decide(request)), RunActions)
    assert told == ["looking"]


def _ends_noted(seen: list[str], *calls: tuple[list[Any], Any]) -> Any:
    """A client like ``_Calls`` that notes in ``seen`` when each reply's stream ends."""

    class Stream(_Stream):
        async def get_final_message(self) -> Any:
            seen.append("reply ends")
            return await super().get_final_message()

    script = list(calls)

    @asynccontextmanager
    async def stream(**request: Any) -> AsyncGenerator[_Stream]:
        events, final = script.pop(0)
        yield Stream(events, final)

    return SimpleNamespace(messages=SimpleNamespace(stream=stream))


def test_the_deadline_moves_when_a_look_up_starts_not_when_the_reply_ends() -> None:
    """#178: after #177 the deadline only moved once the whole reply had ended, so a
    reply still streaming past the usual deadline failed where it used to get more time."""
    seen: list[str] = []
    client = _ends_noted(seen, _looks_up(), ([], _final("Area 18.")))
    brain = ClaudeBrain(MATCHER, client=client, lookups=(FakeLookup(),))
    request = BrainRequest("where do I sell gold", KEYBINDS, looking=lambda: seen.append("looking"))
    assert asyncio.run(brain.decide(request)) == Reply("Area 18.")
    assert seen == ["looking", "reply ends", "reply ends"]


def test_checking_is_said_once_after_the_reply_ends_without_an_act() -> None:
    seen: list[str] = []
    calls = [_looks_up(use_id=f"toolu_{n}") for n in range(2)]
    client = _ends_noted(seen, *calls, ([], _final("Area 18.")))
    brain = ClaudeBrain(MATCHER, client=client, lookups=(FakeLookup(),))
    request = BrainRequest(
        "where do I sell gold",
        KEYBINDS,
        looking=lambda: seen.append("looking"),
        checking=lambda: seen.append("checking"),
    )
    assert asyncio.run(brain.decide(request)) == Reply("Area 18.")
    assert seen == ["looking", "reply ends", "checking", "looking", "reply ends", "reply ends"]


def test_look_ups_in_one_reply_run_at_the_same_time() -> None:
    """#176: two look-ups in a reply take as long as one, not twice as long."""

    class Slow(FakeLookup):
        now = 0
        most = 0

        async def run(self, arguments: Any) -> Any:
            Slow.now += 1
            Slow.most = max(Slow.most, Slow.now)
            await asyncio.sleep(0.05)
            Slow.now -= 1
            return await super().run(arguments)

    both = _reply(
        _tool_stop("ship_lookup", {"name": "Cutlass"}, "toolu_1"),
        _tool_stop("trade_routes", {"start": "Lorville"}, "toolu_2"),
    )
    client = _Calls(both, ([], _final("Haul laranite.")))
    assert _ask(client, Slow("ship_lookup"), Slow("trade_routes")) == Reply("Haul laranite.")
    assert Slow.most == 2  # both running at once, without timing anything


def test_a_refused_act_long_ago_isnt_blamed_for_running_out_of_calls() -> None:
    """Review of #177: refused on call 1, look-ups after: "still looking things up"."""
    calls = [_picks(["v_unbound"], "toolu_0"), *(_looks_up(use_id=f"toolu_{n}") for n in (1, 2))]
    with pytest.raises(BrainError, match="still looking"):
        _ask(_Calls(*calls), FakeLookup())


def test_a_capitalised_tool_call_in_text_isnt_spoken() -> None:
    with pytest.raises(BrainError, match="tool call as text"):
        _ask(_Calls(([], _final('Show_card({"say": "x"})'))))


def _Credited(name: str, answer: str = '{"sell": []}') -> FakeLookup:
    return FakeLookup(name, answer, credit="UEX")


def test_an_answer_from_uex_names_it_if_claude_didnt() -> None:
    """Eval (#164): with the details on a card, the spoken line dropped "UEX"."""
    client = _Calls(_looks_up("commodity_prices"), ([], _final("Orison pays the most.")))
    assert _ask(client, _Credited("commodity_prices")) == Reply("Orison pays the most. From UEX.")


def test_an_answer_that_names_uex_is_left_alone() -> None:
    client = _Calls(_looks_up("commodity_prices"), ([], _final("UEX has Orison paying most.")))
    assert _ask(client, _Credited("commodity_prices")) == Reply("UEX has Orison paying most.")


def test_an_answer_without_a_credited_look_up_is_left_alone() -> None:
    client = _Calls(_looks_up(), ([], _final("The Andromeda.")))
    assert _ask(client, FakeLookup()) == Reply("The Andromeda.")


def test_a_card_answer_from_uex_names_it() -> None:
    """#164's case: the details on a card, and the spoken line without UEX."""
    card = _tool_stop(
        "show_card",
        {
            "say": "Orison pays the most.",
            "kind": "list",
            "title": "Gold",
            "rows": [{"text": "Orison"}],
        },
        "toolu_2",
    )
    client = _Calls(_looks_up("commodity_prices"), ([card], None))
    decision = _ask(client, _Credited("commodity_prices"))
    assert isinstance(decision, Reply)
    assert decision.text == "Orison pays the most. From UEX."
    assert decision.card is not None


def test_a_uex_miss_or_question_back_isnt_credited() -> None:
    """Review of #179: "Where are you starting from? From UEX." after a miss."""
    miss = _Credited("trade_routes", '{"not_found": "no start or commodity"}')
    client = _Calls(_looks_up("trade_routes"), ([], _final("Where are you starting from?")))
    assert _ask(client, miss) == Reply("Where are you starting from?")


def test_uex_said_with_dots_is_a_credit() -> None:
    client = _Calls(_looks_up("commodity_prices"), ([], _final("U.E.X. has Orison top.")))
    assert _ask(client, _Credited("commodity_prices")) == Reply("U.E.X. has Orison top.")


def test_the_site_named_is_a_credit_too() -> None:
    client = _Calls(_looks_up("commodity_prices"), ([], _final("Per uexcorp.space, Orison.")))
    assert _ask(client, _Credited("commodity_prices")) == Reply("Per uexcorp.space, Orison.")


def _refused_beside_a_look_up(seen: list[str], then: tuple[list[Any], Any]) -> Any:
    """A request whose first reply has a look-up and an act naming an unbound action."""
    first = _reply(
        _tool_stop("ship_lookup", {"name": "Cutlass"}, "toolu_1"),
        _tool_stop("run_actions", {"actions": ["v_unbound"], "say": "Done."}, "toolu_2"),
    )
    brain = ClaudeBrain(MATCHER, client=cast(Any, _Calls(first, then)), lookups=(FakeLookup(),))
    request = BrainRequest(
        "gear",
        KEYBINDS,
        looking=lambda: seen.append("looking"),
        checking=lambda: seen.append("checking"),
    )
    return asyncio.run(brain.decide(request))


def test_no_checking_line_just_before_a_press_after_a_refused_act() -> None:
    """#214: the reply with the refused act ended without a press, so "Checking." was
    said, and the next call pressed straight after it."""
    seen: list[str] = []
    decided = _refused_beside_a_look_up(seen, _picks(["v_toggle_landing_system"], "toolu_3"))
    assert isinstance(decided, RunActions)
    assert seen == ["looking"]


def test_a_checking_line_held_back_isnt_said_late_before_the_answer() -> None:
    """Review of #241: owed to the next reply, it played once the results were in, and
    held the answer up by its own length."""
    seen: list[str] = []
    assert _refused_beside_a_look_up(seen, ([], _final("Gear's not bound."))) == Reply(
        "Gear's not bound."
    )
    assert seen == ["looking"]


def test_closing_the_brain_closes_its_look_ups() -> None:
    """#240: a fetch that outlived its look-up was still pending when the loop closed."""

    class Closing(FakeLookup):
        closed = 0

        async def close(self) -> None:
            Closing.closed += 1

    lent = _Client([], _final(""))
    brain = ClaudeBrain(MATCHER, client=cast(Any, lent), lookups=(Closing(), FakeLookup("plain")))
    asyncio.run(brain.close())
    assert Closing.closed == 1


def test_an_empty_run_actions_with_a_line_is_a_reply_through_the_adapter() -> None:
    """#279 (review of #278): run_actions with no actions and a say line is a Reply, not
    an error, all the way through ClaudeBrain; and a look-up started in the same reply
    is cancelled, not left running."""
    cancelled: list[str] = []

    class Hanging(FakeLookup):
        async def run(self, arguments: Any) -> Any:
            try:
                return await super().run(arguments)
            except asyncio.CancelledError:
                cancelled.append("ship_lookup")
                raise

    class Streaming(_Stream):
        """Events arrive over time, as from the network, so the look-up gets going."""

        def __aiter__(self) -> AsyncIterator[Any]:
            async def events() -> AsyncIterator[Any]:
                for event in self._events:  # pyright: ignore[reportPrivateUsage]
                    await asyncio.sleep(0.01)
                    yield event

            return events()

    lookup = _tool_stop("ship_lookup", {"name": "Cutlass"}, "toolu_1")
    chat = _tool_stop("run_actions", {"actions": [], "say": "Copy."}, "toolu_2")
    events, final = _reply(lookup, chat)
    requests: list[dict[str, Any]] = []

    @asynccontextmanager
    async def stream(**request: Any) -> AsyncGenerator[_Stream]:
        requests.append(request)
        yield Streaming(events, final)

    client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    brain = ClaudeBrain(MATCHER, client=cast(Any, client), lookups=(Hanging(hang=True),))

    async def scenario() -> Any:
        decided = await brain.decide(BrainRequest("nice shot, squad", KEYBINDS))
        await asyncio.sleep(0)  # let the cancelled look-up see its cancellation
        return decided

    assert asyncio.run(scenario()) == Reply("Copy.")
    assert cancelled == ["ship_lookup"]
    assert len(requests) == 1  # answered in one call: nothing waited for the look-up
