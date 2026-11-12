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

from tycho.knowledge.web import MAX_ANSWER, SECONDS, SITES, WEB_SEARCH, WebSearch

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


def test_a_lead_in_before_the_search_stays_apart_from_the_answer() -> None:
    message = _found("RSI has 4.11 for Q4.")
    message.content.insert(0, SimpleNamespace(type="text", text="I'll search for that."))
    _, result = _search(_Client(message))
    assert result["answer"] == "I'll search for that. RSI has 4.11 for Q4."


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
