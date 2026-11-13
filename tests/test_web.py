"""Web search (ADR 15): the look-up that searches Star Citizen sites in a request of
its own. What the brain does around it is in test_brain."""

import asyncio
import datetime
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest

from tycho.knowledge.web import (
    FALLBACK_ASK,
    FALLBACK_BRIEF,
    MAX_ANSWER,
    OPEN_BRIEF,
    OPEN_PAGE_TOKENS,
    OPEN_SECONDS,
    SECONDS,
    SITES,
    WEB_FETCH,
    WEB_SEARCH,
    WebSearch,
)

# --- The search request -------------------------------------------------------------------------


class _Client:
    """Answers the search request with ``message``, or raises ``error``."""

    def __init__(self, message: Any = None, error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []

        async def create(**request: Any) -> Any:
            self.requests.append(request)
            if error is not None:
                raise error
            return message

        @asynccontextmanager
        async def stream(**request: Any) -> AsyncGenerator[None]:  # unused
            yield None

        self.messages = SimpleNamespace(create=create, stream=stream)


def _found(*texts: str, stop_reason: str = "end_turn") -> Any:
    results = SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(url="https://robertsspaceindustries.com/roadmap/release-view"),
            SimpleNamespace(url="https://starcitizen.tools/Kraken"),
        ],
    )
    content = [
        SimpleNamespace(type="server_tool_use"),
        results,
        *(SimpleNamespace(type="text", text=t) for t in texts),
    ]
    usage = SimpleNamespace(input_tokens=15000, output_tokens=120)
    return SimpleNamespace(content=content, usage=usage, stop_reason=stop_reason)


def _search(client: _Client, query: object = "Star Citizen 4.11") -> tuple[bool, Any]:
    search = WebSearch(cast(Any, client), today=lambda: datetime.date(2026, 11, 2))
    result = asyncio.run(search.run({"query": query}))
    return result.is_error, json.loads(result.content)


def test_it_searches_only_star_citizen_sites_once() -> None:
    client = _Client(_found("RSI's roadmap has 4.11 for Q4 2026."))
    error, result = _search(client)
    assert not error
    (request,) = client.requests
    assert request["tools"] == [
        {
            "type": WEB_SEARCH,
            "name": "web_search",
            "max_uses": 1,
            "allowed_domains": list(SITES),
        }
    ]
    assert "robertsspaceindustries.com" in SITES
    assert request["messages"] == [
        {"role": "user", "content": "[2 November 2026] Star Citizen 4.11"}
    ]
    assert result == {
        "source": "web search of Star Citizen sites",
        "searched": "Star Citizen 4.11",
        "date": "2026-11-02",
        "answer": "RSI's roadmap has 4.11 for Q4 2026.",
        "sites": [
            "https://robertsspaceindustries.com/roadmap/release-view",
            "https://starcitizen.tools/Kraken",
        ],
    }


def test_the_answer_is_one_spoken_line() -> None:
    _, result = _search(_Client(_found("RSI's roadmap has ", " 4.11 for Q4 ", ".\n")))
    assert result["answer"] == "RSI's roadmap has 4.11 for Q4."


def test_cited_text_joins_without_a_space_before_punctuation() -> None:
    # Cited text comes in blocks that split mid-sentence (#366).
    _, result = _search(
        _Client(_found("Remote turrets have no way in for players", ", and run from a seat", "."))
    )
    assert result["answer"] == "Remote turrets have no way in for players, and run from a seat."


def test_the_reply_has_room_for_thinking_and_the_answer() -> None:
    # With 400, half the answers stopped mid-word (#366).
    client = _Client(_found("RSI's roadmap has 4.11 for Q4 2026."))
    _search(client)
    assert client.requests[0]["max_tokens"] >= 2000


def test_an_answer_cut_off_keeps_only_whole_sentences() -> None:
    cut = _found(
        "Land on a pad and open Vehicle Services. Select Repair, Restock, Ref",
        stop_reason="max_tokens",
    )
    _, result = _search(_Client(cut))
    assert result["answer"] == "Land on a pad and open Vehicle Services."
    error, result = _search(
        _Client(_found("Open the Contract Manager and accept a salv", stop_reason="max_tokens"))
    )
    assert error
    assert "nothing usable" in result["error"]


def test_a_lead_in_before_the_search_is_left_out_of_the_answer() -> None:
    message = _found("RSI has 4.11 for Q4.")
    message.content.insert(0, SimpleNamespace(type="text", text="I'll search for that."))
    _, result = _search(_Client(message))
    assert result["answer"] == "RSI has 4.11 for Q4."
    # Not even as the only whole sentence of a cut answer (#370).
    cut = _found("RSI has 4.11 for Q", stop_reason="max_tokens")
    cut.content.insert(0, SimpleNamespace(type="text", text="I'll search for that."))
    error, result = _search(_Client(cut))
    assert error


def test_a_cut_answer_does_not_stop_at_an_abbreviation() -> None:
    cut = _found("Patch 4.11 is due. It lands Dec. 12 and adds the Kra", stop_reason="max_tokens")
    _, result = _search(_Client(cut))
    assert result["answer"] == "Patch 4.11 is due."


def test_a_long_answer_ends_on_a_whole_sentence() -> None:
    sentence = "Quantum fuel is sold at Megumi Refueling and both Pyro gateways. "
    _, result = _search(_Client(_found(sentence * 12)))
    assert len(result["answer"]) <= MAX_ANSWER
    assert result["answer"].endswith("gateways.")
    _, result = _search(_Client(_found("word " * 200)))
    assert len(result["answer"]) <= MAX_ANSWER
    assert result["answer"].endswith("word")


def test_why_the_reply_stopped_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="tycho.knowledge.web")
    _search(_Client(_found("Half an answ", stop_reason="max_tokens")))
    assert "stopped: max_tokens" in caplog.text


def test_a_failed_or_empty_search_says_so() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    down = _Client(error=anthropic.APIConnectionError(request=request))
    error, result = _search(down)
    assert error
    assert "failed" in result["error"]
    error, result = _search(_Client(_found()))
    assert error
    assert "nothing usable" in result["error"]
    error, result = _search(_Client(_found("x")), query="  ")
    assert error
    assert "query" in result["error"]


def test_the_brain_treats_it_as_from_the_web() -> None:
    search = WebSearch(cast(Any, _Client()))
    assert search.from_the_web
    assert search.seconds == SECONDS
    assert search.input_schema["additionalProperties"] is False
    assert search.name == "web_search"


@pytest.mark.parametrize("kept", [False, True])
def test_the_query_reaches_the_log_only_with_transcripts_kept(
    caplog: pytest.LogCaptureFixture, kept: bool
) -> None:
    # It's the player's question, reworded (audit, 2026-11-03).
    caplog.set_level(logging.DEBUG, logger="tycho.knowledge.web")
    client = _Client(_found("RSI's roadmap has 4.11 for Q4 2026."))
    search = WebSearch(cast(Any, client), log_queries=kept)
    asyncio.run(search.run({"query": "is the Polaris getting nerfed"}))
    assert ("Polaris" in caplog.text) is kept
    assert "Web search" in caplog.text


# --- The open-web trial (#354) ------------------------------------------------------------------


def _read_page(*texts: str, paused: bool = False) -> Any:
    """A search, a page read, then the answer; or, ``paused``, only the preamble."""
    preamble = SimpleNamespace(type="text", text="Let me look that up.")
    search = [
        SimpleNamespace(type="server_tool_use", name="web_search"),
        SimpleNamespace(
            type="web_search_tool_result",
            content=[SimpleNamespace(url="https://www.reddit.com/r/starcitizen/levski")],
        ),
    ]
    read = [
        SimpleNamespace(type="server_tool_use", name="web_fetch"),
        SimpleNamespace(
            type="web_fetch_tool_result",
            content=SimpleNamespace(url="https://starcitizen.tools/Levski"),
        ),
    ]
    content = (
        [preamble, *search]
        if paused
        else [*read, *(SimpleNamespace(type="text", text=t) for t in texts)]
    )
    usage = SimpleNamespace(input_tokens=9000, output_tokens=80)
    return SimpleNamespace(
        content=content, usage=usage, stop_reason="pause_turn" if paused else "end_turn"
    )


class _Turns(_Client):
    """Answers each search request with the next message; an exception is raised, and
    ``SLOW`` takes longer than any test waits."""

    def __init__(self, *messages: Any) -> None:
        super().__init__()
        left = list(messages)

        async def create(**request: Any) -> Any:
            self.requests.append(request)
            message = left.pop(0)
            if message is SLOW:
                await asyncio.sleep(30)
            if isinstance(message, Exception):
                raise message
            return message

        self.messages = SimpleNamespace(create=create)


def test_the_open_web_trial_searches_any_site_and_reads_pages() -> None:
    client = _Client(_read_page("Players report the fines terminal is in the admin office."))
    search = WebSearch(cast(Any, client), open_web=True)
    result = json.loads(asyncio.run(search.run({"query": "Levski pay fines"})).content)
    (request,) = client.requests
    assert request["system"] == OPEN_BRIEF
    assert request["tools"] == [
        {"type": WEB_SEARCH, "name": "web_search", "max_uses": 2},
        {
            "type": WEB_FETCH,
            "name": "web_fetch",
            "max_uses": 2,
            "max_content_tokens": OPEN_PAGE_TOKENS,
            "blocked_domains": ["reddit.com"],
        },
    ]
    assert result["answer"] == "Players report the fines terminal is in the admin office."
    assert result["sites"] == ["https://starcitizen.tools/Levski"]
    assert "any site" in result["source"]
    assert search.seconds == OPEN_SECONDS > SECONDS
    assert "how to do something" in search.description


def test_the_open_web_goes_on_after_a_paused_turn_and_keeps_only_the_answer() -> None:
    client = _Turns(_read_page(paused=True), _read_page("It's in the admin office."))
    search = WebSearch(cast(Any, client), open_web=True)
    result = json.loads(asyncio.run(search.run({"query": "Levski pay fines"})).content)
    first, second = client.requests
    assert second["messages"][-1]["role"] == "assistant"
    assert len(second["messages"]) == len(first["messages"]) + 1
    # "Let me look that up." came before the search: not part of the answer.
    assert result["answer"] == "It's in the admin office."
    assert result["sites"] == [
        "https://starcitizen.tools/Levski",
        "https://www.reddit.com/r/starcitizen/levski",
    ]


def test_without_the_trial_a_paused_turn_is_not_continued() -> None:
    client = _Turns(_read_page(paused=True), _read_page("unused"))
    error, result = _search(client)
    assert len(client.requests) == 1
    assert error
    assert "nothing usable" in result["error"]


# --- Sites first, then the open web (#354) ------------------------------------------------------


SLOW = object()


def _fallback(*messages: Any, seconds: float = OPEN_SECONDS) -> tuple[_Turns, bool, Any]:
    client = _Turns(*messages)
    search = WebSearch(cast(Any, client), fallback=True)
    assert search.seconds == OPEN_SECONDS
    search.seconds = seconds
    result = asyncio.run(search.run({"query": "Levski pay fines"}))
    return client, result.is_error, json.loads(result.content)


def test_a_clear_answer_from_the_game_sites_needs_no_open_web() -> None:
    client, error, result = _fallback(
        _found("FOUND: The fines terminal is in Levski's admin office.")
    )
    assert not error
    (request,) = client.requests
    assert request["tools"][0]["allowed_domains"] == list(SITES)
    assert "FOUND" in request["system"]
    assert result["answer"] == "The fines terminal is in Levski's admin office."
    assert result["source"] == "web search of Star Citizen sites"


@pytest.mark.parametrize("verdict", ["PARTIAL", "NONE"])
def test_without_a_clear_answer_it_reads_the_open_web(verdict: str) -> None:
    client, error, result = _fallback(
        _found(f"{verdict}: Fines are paid at admin offices."),
        _read_page("Players report the fines terminal is by Levski's hangars."),
    )
    assert not error
    first, wider = client.requests
    assert "allowed_domains" in first["tools"][0]
    assert wider["system"] == FALLBACK_BRIEF + FALLBACK_ASK
    # The open web is told what the sites said, to keep what's right and add to it.
    told = wider["messages"][0]["content"].endswith(
        "What the game's sites said: Fines are paid at admin offices."
    )
    assert told is (verdict == "PARTIAL")
    assert [t["name"] for t in wider["tools"]] == ["web_search", "web_fetch"]
    assert "allowed_domains" not in wider["tools"][0]
    assert result["answer"] == "Players report the fines terminal is by Levski's hangars."
    assert result["source"] == "web search, any site"


def test_a_partial_answer_stands_when_the_open_web_finds_nothing() -> None:
    _, error, result = _fallback(_found("PARTIAL: Fines are paid at admin offices."), _read_page())
    assert not error
    assert result["answer"] == "Fines are paid at admin offices."
    _, error, result = _fallback(_found("NONE: Nothing about Levski."), _read_page())
    assert error
    assert "nothing usable" in result["error"]


@pytest.mark.parametrize(
    "answer",
    ["None of RSI's posts give a date, but the roadmap says Q1 2027.", "Foundry 42 said so."],
)
def test_only_a_fallback_reads_a_found_partial_or_none_tag(answer: str) -> None:
    # An answer that merely starts with "None" or "Found" is an answer (#372 review).
    _, result = _search(_Client(_found(answer)))
    assert result["answer"] == answer
    _, _, result = _fallback(_found(f"PARTIAL: {answer}"), _read_page())
    assert result["answer"] == answer


def test_the_sites_answer_stands_when_the_open_web_does_worse() -> None:
    # #372 benchmark: a PARTIAL from the sites became "I couldn't find it".
    _, error, result = _fallback(
        _found("PARTIAL: Fines are paid at admin offices."),
        _read_page("NONE: I couldn't find where in Levski."),
    )
    assert not error
    assert result["answer"] == "Fines are paid at admin offices."
    assert result["source"] == "web search of Star Citizen sites"
    _, _, result = _fallback(
        _found("PARTIAL: Fines are paid at admin offices."),
        _read_page("PARTIAL: Players report it's near the hangars."),
    )
    assert result["answer"] == "Players report it's near the hangars."


@pytest.mark.parametrize(
    "failing",
    [
        SLOW,
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
    ],
)
def test_a_failed_or_slow_open_web_keeps_the_sites_answer(failing: object) -> None:
    # #373: the look-up's own time limit would otherwise throw the answer away.
    _, error, result = _fallback(
        _found("PARTIAL: Fines are paid at admin offices."), failing, seconds=1.2
    )
    assert not error
    assert result["answer"] == "Fines are paid at admin offices."


@pytest.mark.parametrize(
    ("reply", "verdict_wider"),
    [
        ("Fines are paid at admin offices. PARTIAL", True),
        ("Fines are paid at admin offices. (NONE)", True),
        ("Fines are paid at admin offices. FOUND.", False),
        ("Fines are paid at admin offices.", True),  # no tag: not a clear answer
    ],
)
def test_a_tag_put_last_is_read_and_never_spoken(reply: str, verdict_wider: bool) -> None:
    client, _, result = _fallback(_found(reply), _read_page("NONE: Nothing more."))
    assert len(client.requests) == (2 if verdict_wider else 1)
    if "NONE" in reply:
        assert "nothing usable" in result["error"]
    else:
        assert result["answer"] == "Fines are paid at admin offices."
