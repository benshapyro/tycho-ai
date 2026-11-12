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


def _found(*texts: str) -> Any:
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
    return SimpleNamespace(content=content, usage=usage)


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
    assert result["answer"] == "RSI's roadmap has 4.11 for Q4 ."


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
    """Answers each search request with the next message."""

    def __init__(self, *messages: Any) -> None:
        super().__init__()
        left = list(messages)

        async def create(**request: Any) -> Any:
            self.requests.append(request)
            return left.pop(0)

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
        },
    ]
    assert result["answer"] == "Players report the fines terminal is in the admin office."
    assert result["sites"] == ["https://starcitizen.tools/Levski"]
    assert "trial" in result["source"]
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
