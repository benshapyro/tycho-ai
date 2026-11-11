"""The OpenRouter brain against a scripted OpenAI-style stream (#192). No key, no spend."""

import asyncio
import datetime
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from tests.fakes.fake_chat import FakeChat, finished, text, tool_call, usage
from tests.fakes.fake_lookup import FakeLookup
from tycho import credentials
from tycho.brain import BrainError, BrainRequest, Reply, RunActions
from tycho.brain.decisions import UnboundAction
from tycho.brain.openrouter import (
    MAX_TOKENS,
    REASONING_TOKENS,
    SAID_CHARACTERS,
    OpenRouterBrain,
    OpenRouterClient,
    Prices,
    chunks,
    route,
)
from tycho.brain.rounds import CUT_SHORT, LAST_CALL, MAX_CALLS, NO_PRESS_AFTER_SEARCH
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Action, Keybinds, KeyInput

MATCHER = CommandMatcher.default()
MODEL = "anthropic/claude-haiku-5.5"


def _action(name: str, key: str | None, label: str) -> Action:
    inputs = () if key is None else (KeyInput.parse(key) or KeyInput(key),)
    return Action(name=name, actionmap="m", label=label, inputs=inputs)


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n", "Landing System (Toggle)"),
        _action("v_eject", "ralt+y", "Eject"),
        _action("v_unbound", None, "Not bound"),
    ]
)
GEAR = {"actions": ["v_toggle_landing_system"], "say": "Gear."}


def _brain(client: FakeChat, *lookups: FakeLookup, **kwargs: Any) -> OpenRouterBrain:
    return OpenRouterBrain(
        MATCHER,
        model=MODEL,
        client=client,
        lookups=lookups,
        today=lambda: datetime.date(2026, 11, 9),
        **kwargs,
    )


def _ask(client: FakeChat, *lookups: FakeLookup, **request: Any) -> Any:
    brain = _brain(client, *lookups)
    return asyncio.run(brain.decide(BrainRequest("gear down", KEYBINDS, **request)))


def test_an_act_is_taken_as_soon_as_its_tool_call_is_complete() -> None:
    # The rest of the stream would fail: the brain must never read that far.
    client = FakeChat([*tool_call("run_actions", GEAR), RuntimeError("read past the act")])
    assert _ask(client) == RunActions(("v_toggle_landing_system",), "Gear.")
    assert client.read == [3]  # the id and name, then the two halves of the arguments


def test_only_the_first_act_in_a_reply_is_pressed() -> None:
    eject = {"actions": ["v_eject"], "say": "Bye."}
    client = FakeChat([*tool_call("run_actions", GEAR), *tool_call("run_actions", eject, index=1)])
    assert _ask(client) == RunActions(("v_toggle_landing_system",), "Gear.")


def test_the_request_is_openai_shaped_with_the_same_briefing_and_tools() -> None:
    client = FakeChat(text("Hello."))
    assert _ask(client) == Reply("Hello.")
    (body,) = client.requests
    assert body["model"] == MODEL
    assert body["stream"] is True
    assert "reasoning" not in body  # each model as its provider runs it, unless asked
    system, user = body["messages"]
    assert system["role"] == "system"
    assert system["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "v_toggle_landing_system" in system["content"][0]["text"]
    assert user["content"][0]["text"] == "[9 November 2026]"
    assert user["content"][1]["text"] == "gear down"
    names = [t["function"]["name"] for t in body["tools"]]
    assert names == ["run_sequence", "run_actions"]
    assert all(t["type"] == "function" for t in body["tools"])


def test_a_model_that_reasons_has_room_to_answer() -> None:
    # GLM, Qwen and Kimi spent all 1024 tokens reasoning and never answered (#268).
    client = FakeChat(text("Hi."))
    asyncio.run(_brain(client).decide(BrainRequest("hi", KEYBINDS)))
    assert client.requests[0]["max_tokens"] == MAX_TOKENS + REASONING_TOKENS


def test_an_effort_is_asked_for_only_when_given() -> None:
    client = FakeChat(text("Hi."))
    asyncio.run(_brain(client, effort="low").decide(BrainRequest("hi", KEYBINDS)))
    assert client.requests[0]["reasoning"] == {"effort": "low", "exclude": True}


def test_no_host_is_pinned_unless_asked() -> None:
    # OpenRouter then picks the host as it always does (mostly on price).
    client = FakeChat(text("Hi."))
    asyncio.run(_brain(client).decide(BrainRequest("hi", KEYBINDS)))
    assert "provider" not in client.requests[0]


def test_pinned_hosts_are_sent_in_order_with_no_fallback() -> None:
    # #267: a fast host is measured only if every call goes to it.
    client = FakeChat(text("Hi."), text("Hi."))
    brain = _brain(client, hosts=["Cerebras", " groq "])
    asyncio.run(brain.decide(BrainRequest("hi", KEYBINDS)))
    asyncio.run(brain.decide(BrainRequest("hello", KEYBINDS)))
    pinned = {"order": ["cerebras", "groq"], "allow_fallbacks": False}
    assert [r["provider"] for r in client.requests] == [pinned, pinned]


def test_the_warm_up_goes_to_the_pinned_host_too() -> None:
    # Otherwise it would warm a cache on a host the run never uses.
    client = FakeChat(text("Hi."))
    asyncio.run(_brain(client, hosts=["groq"]).warm(KEYBINDS))
    assert client.requests[0]["provider"] == {"order": ["groq"], "allow_fallbacks": False}


def test_a_host_can_be_picked_by_latency() -> None:
    assert route(sort="latency") == {"sort": "latency"}
    assert route(["cerebras"], "latency") == {
        "order": ["cerebras"],
        "allow_fallbacks": False,
        "sort": "latency",
    }
    with pytest.raises(ValueError, match="sort"):
        _brain(FakeChat(), sort="fastest")


def test_each_call_logs_the_host_that_served_it(caplog: pytest.LogCaptureFixture) -> None:
    # #267: a latency means little without who served it.
    served = [{**c, "provider": "Cerebras"} for c in tool_call("run_actions", GEAR)]
    reply = [{**c, "provider": "Groq"} for c in text("Hi.")]
    caplog.set_level(logging.DEBUG, "tycho.brain")
    assert _ask(FakeChat(served)) == RunActions(("v_toggle_landing_system",), "Gear.")
    assert _ask(FakeChat(reply)) == Reply("Hi.")
    said = [r.getMessage() for r in caplog.records]
    lines = [m for m in said if m.startswith(("Brain acted", "Brain replied"))]
    assert len(lines) == 2
    assert lines[0].endswith("generation gen-1 [host: Cerebras]")
    assert lines[1].endswith("(reported) [host: Groq]")


def test_cards_are_offered_only_while_the_tile_is_on() -> None:
    client = FakeChat(text("Hi."), text("Hi."))
    brain = _brain(client, FakeLookup(), cards=lambda: True)
    asyncio.run(brain.decide(BrainRequest("hi", KEYBINDS)))
    names = [t["function"]["name"] for t in client.requests[0]["tools"]]
    assert names == ["run_sequence", "run_actions", "show_card", "ship_lookup"]


def test_a_look_up_goes_back_as_a_tool_message_and_the_model_answers() -> None:
    lookup = FakeLookup()
    looked: list[str] = []
    client = FakeChat(
        [*tool_call("ship_lookup", {"name": "Andromeda"}), *finished()],
        text("RSI ", "makes it."),
    )
    decision = _ask(
        client,
        lookup,
        looking=lambda: looked.append("looking"),
        checking=lambda: looked.append("checking"),
    )
    assert decision == Reply("RSI makes it.")
    assert lookup.asked == [{"name": "Andromeda"}]
    assert looked == ["looking", "checking"]
    first, second = client.requests
    assert first["tools"] == second["tools"]  # the same tools every call, so the cache holds
    assistant, result = second["messages"][-2:]
    assert assistant["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "ship_lookup", "arguments": '{"name": "Andromeda"}'},
        }
    ]
    assert result == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"name": "Constellation Andromeda", "maker": "RSI"}',
    }


def test_before_the_last_call_the_model_is_told_to_answer() -> None:
    calls = [
        [*tool_call("ship_lookup", {"name": "Andromeda"}, use_id=f"call_{n}"), *finished()]
        for n in range(2)
    ]
    client = FakeChat(*calls, text("RSI."))
    assert _ask(client, FakeLookup()) == Reply("RSI.")
    assert client.requests[2]["messages"][-1] == {"role": "user", "content": LAST_CALL}


def test_an_act_after_a_look_up_is_still_checked() -> None:
    client = FakeChat(
        [*tool_call("ship_lookup", {"name": "Andromeda"}), *finished()],
        tool_call("run_actions", GEAR, use_id="call_2"),
    )
    assert _ask(client, FakeLookup()) == RunActions(("v_toggle_landing_system",), "Gear.")


def test_an_unbound_action_is_refused_and_never_pressed() -> None:
    bad = {"actions": ["v_unbound"], "say": "Done."}
    client = FakeChat(
        [*tool_call("run_actions", bad), *finished()], text("There's no key for that.")
    )
    assert _ask(client) == Reply("There's no key for that.")
    (result,) = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert "Nothing was pressed" in result["content"]


def test_unbound_picks_to_the_end_fail_as_unbound() -> None:
    bad = {"actions": ["v_unbound"], "say": "Done."}
    calls = [[*tool_call("run_actions", bad, use_id=f"call_{n}"), *finished()] for n in range(3)]
    with pytest.raises(UnboundAction):
        _ask(FakeChat(*calls))
    assert MAX_CALLS == 3


@pytest.mark.parametrize("arguments", ['{"actions": ["v_toggle_landing', "not json"])
def test_a_malformed_act_fails_and_presses_nothing(arguments: str) -> None:
    client = FakeChat([*tool_call("run_actions", arguments), *finished()])
    with pytest.raises(BrainError, match="aren't an object"):
        _ask(client)


def test_a_malformed_look_up_becomes_an_error_result() -> None:
    lookup = FakeLookup()
    client = FakeChat(
        [*tool_call("ship_lookup", '{"name": '), *finished()], text("I couldn't check.")
    )
    assert _ask(client, lookup) == Reply("I couldn't check.")
    assert lookup.asked == []
    (result,) = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert "must be an object" in result["content"]


@pytest.mark.parametrize("arguments", ['{"name": ', "not json", '["Andromeda"]'])
def test_a_malformed_look_up_goes_back_as_valid_json(arguments: str) -> None:
    # A real provider refuses a turn whose tool call holds invalid JSON (#260).
    client = FakeChat(
        [*tool_call("ship_lookup", arguments), *finished()], text("I couldn't check.")
    )
    assert _ask(client, FakeLookup()) == Reply("I couldn't check.")
    (turn,) = [m for m in client.requests[1]["messages"] if m.get("tool_calls")]
    (call,) = turn["tool_calls"]
    assert json.loads(call["function"]["arguments"]) == {}
    assert call["id"] == "call_1"  # still paired with its error result


def test_nothing_presses_after_a_web_search() -> None:
    web = FakeLookup(name="web_search", answer='{"results": []}', from_the_web=True)
    client = FakeChat(
        [*tool_call("web_search", {"name": "x"}), *finished()],
        tool_call("run_actions", GEAR, use_id="call_2"),
    )
    assert _ask(client, web) == Reply(NO_PRESS_AFTER_SEARCH)


def test_a_tool_call_written_as_text_is_never_said() -> None:
    client = FakeChat(text('Here. show_card({"say": "x"})'))
    assert _ask(client) == Reply("Here.")


def test_an_error_in_the_stream_fails_the_request() -> None:
    client = FakeChat([{"error": {"message": "Provider returned error", "code": 502}}])
    with pytest.raises(BrainError, match="Provider returned error"):
        _ask(client)


def test_a_refusal_fails_the_request() -> None:
    with pytest.raises(BrainError, match="declined"):
        _ask(FakeChat(text("", finish="content_filter")))


def test_the_reported_cost_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="tycho.brain"):
        _ask(FakeChat(text("Hi.", cost=0.00042)))
    assert "about $0.00042 (reported)" in caplog.text


def test_without_a_reported_cost_the_price_list_is_used(caplog: pytest.LogCaptureFixture) -> None:
    prices = Prices(prompt=1e-6, completion=5e-6, cache_read=1e-7)
    brain = _brain(FakeChat(text("Hi.", cost=None)), prices=prices)
    with caplog.at_level(logging.DEBUG, logger="tycho.brain"):
        asyncio.run(brain.decide(BrainRequest("hi", KEYBINDS)))
    # 200 uncached at 1e-6, 800 cached at 1e-7, 20 out at 5e-6
    assert "about $0.00038 (price list)" in caplog.text


def test_an_act_logs_its_generation_so_its_cost_can_be_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="tycho.brain"):
        _ask(FakeChat(tool_call("run_actions", GEAR)))
    assert "generation gen-1" in caplog.text


def test_warming_sends_the_same_briefing_and_never_raises() -> None:
    client = FakeChat([usage()], [RuntimeError("never asked")])
    brain = _brain(client, FakeLookup())
    asyncio.run(brain.warm(KEYBINDS))
    asyncio.run(brain.close())
    (body,) = client.requests
    assert body["max_tokens"] == 16
    assert body["messages"][0]["role"] == "system"
    assert not client.closed  # the test's own client


def test_a_failed_warm_up_is_only_logged() -> None:
    brain = _brain(FakeChat())  # no replies: the fake raises BrainError
    asyncio.run(brain.warm(KEYBINDS))


def test_it_needs_a_key_or_a_client() -> None:
    with pytest.raises(ValueError, match="key"):
        OpenRouterBrain(MATCHER, model=MODEL)


# --- The HTTP client --------------------------------------------------------------------


def _client(handler: Any) -> OpenRouterClient:
    http = httpx2.AsyncClient(
        base_url="https://openrouter.ai/api/v1", transport=httpx2.MockTransport(handler)
    )
    return OpenRouterClient("sk-or-v1-test", client=http)


async def _read(client: OpenRouterClient) -> list[Any]:
    async with client.stream({"model": MODEL}) as stream:
        return [chunk async for chunk in stream]


def test_the_client_reads_server_sent_events() -> None:
    sent: list[httpx2.Request] = []
    body = "\n".join(
        [
            ": OPENROUTER PROCESSING",
            "",
            'data: {"id": "gen-1", "choices": []}',
            "data: not json",
            'data: {"id": "gen-2", "choices": []}',
            "data: [DONE]",
            'data: {"id": "after"}',
        ]
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, text=body)

    read = asyncio.run(_read(_client(handler)))
    assert [c["id"] for c in read] == ["gen-1", "gen-2"]
    (request,) = sent
    assert request.url.path == "/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-or-v1-test"
    assert json.loads(request.content) == {"model": MODEL}


@pytest.mark.parametrize(
    ("status", "problem"),
    [(401, "key was refused"), (402, "out of credit"), (500, r"error \(500\)$")],
)
def test_the_client_explains_a_failed_request(status: int, problem: str) -> None:
    def fail(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text='{"error": {}}')

    client = _client(fail)
    with pytest.raises(BrainError, match=problem):
        asyncio.run(_read(client))


def test_the_client_says_why_openrouter_refused() -> None:
    # A bare "error (404)" hid that the account's privacy setting refused the model (#268).
    said = "No endpoints found matching your data policy.\n ZDR violation (account settings)"

    def refuse(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"error": {"message": said, "code": 404}})

    with pytest.raises(BrainError) as raised:
        asyncio.run(_read(_client(refuse)))
    assert str(raised.value) == (
        "OpenRouter returned an error (404): No endpoints found matching your data "
        "policy. ZDR violation (account settings)"
    )


def test_a_long_error_is_cut_short() -> None:
    def refuse(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": {"message": "y" + "x" * 1000}})

    with pytest.raises(BrainError) as raised:
        asyncio.run(_read(_client(refuse)))
    said = str(raised.value).split(": ", 1)[1]
    assert said == "y" + "x" * (SAID_CHARACTERS - 1)  # exactly the first SAID_CHARACTERS (#270)


@pytest.mark.parametrize("timeout", [httpx2.ConnectTimeout, httpx2.PoolTimeout])
def test_a_network_that_drops_packets_isnt_called_slow(
    timeout: type[httpx2.TimeoutException],
) -> None:
    # #270: a connect or pool timeout means OpenRouter can't be reached, not a slow model.
    def dropped(request: httpx2.Request) -> httpx2.Response:
        raise timeout("timed out", request=request)

    with pytest.raises(BrainError, match="can't reach OpenRouter"):
        asyncio.run(_read(_client(dropped)))


def test_a_slow_model_is_not_called_offline() -> None:
    # Gemini's slow first tokens read as "can't reach OpenRouter (offline?)" (#268).
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with pytest.raises(BrainError, match="too slow"):
        asyncio.run(_read(_client(slow)))


def test_the_client_says_when_openrouter_cant_be_reached() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no network", request=request)

    with pytest.raises(BrainError, match="can't reach OpenRouter"):
        asyncio.run(_read(_client(refuse)))


def test_chunks_stop_at_done() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in ['data: {"a": 1}', "data: [DONE]", 'data: {"b": 2}']:
            yield line

    async def read() -> list[Any]:
        return [c async for c in chunks(lines())]

    assert asyncio.run(read()) == [{"a": 1}]


# --- The key ---------------------------------------------------------------------------


def test_the_openrouter_key_is_one_tycho_stores() -> None:
    assert "openrouter" in credentials.KNOWN
    assert credentials.masked("sk-or-v1-" + "a" * 60) == "sk-or-v1-...aaaa"


def test_a_card_cut_off_at_the_output_limit_says_so(caplog: pytest.LogCaptureFixture) -> None:
    # #266: Haiku's "help me plan a bounty hunting session" through OpenRouter ran past
    # max_tokens mid card and failed with "show_card: the arguments aren't an object".
    card = '{"say": "Plan on the Tile.", "kind": "steps", "rows": [{"text": "Get a lic'
    client = FakeChat([*tool_call("show_card", card), *finished("length")])
    brain = _brain(client, cards=lambda: True)
    caplog.set_level(logging.DEBUG, "tycho.brain")
    decided = asyncio.run(brain.decide(BrainRequest("plan a bounty run", KEYBINDS)))
    assert decided == Reply(CUT_SHORT)
    assert any(r.getMessage().startswith("Brain replied") for r in caplog.records)  # cost


def test_a_whole_card_at_the_output_limit_is_still_shown() -> None:
    card = {
        "say": "Plan on the Tile.",
        "kind": "steps",
        "label": "",
        "title": "Bounty run",
        "source": "",
        "rows": [{"text": "Get a licence", "detail": "", "value": "", "dim": False}],
        "hero_label": "",
        "hero_value": "",
        "text": "",
    }
    client = FakeChat([*tool_call("show_card", card), *finished("length")])
    decided = asyncio.run(
        _brain(client, cards=lambda: True).decide(BrainRequest("plan a bounty run", KEYBINDS))
    )
    assert isinstance(decided, Reply)
    assert decided.card is not None
