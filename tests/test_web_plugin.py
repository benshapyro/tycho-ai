"""Web search through OpenRouter's web plugin (ADR 15, #365): the request it sends and
what it makes of the answer, against a scripted OpenRouter."""

import asyncio
import datetime
import json
from typing import Any

import httpx2
import pytest

from tycho.knowledge import web
from tycho.knowledge.web_plugin import ENGINE, MODEL, PluginSearch, spoken_plainly
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)


def _reply(content: str, finish: str = "stop", urls: tuple[str, ...] = ()) -> dict[str, Any]:
    notes = [{"type": "url_citation", "url_citation": {"url": u}} for u in urls]
    return {
        "choices": [
            {"finish_reason": finish, "message": {"content": content, "annotations": notes}}
        ],
        "usage": {"cost": 0.0057},
    }


def _search(
    reply: dict[str, Any] | None = None,
    *,
    status: int = 200,
    open_web: bool = True,
    fallback: Any = None,
) -> tuple[PluginSearch, list[dict[str, Any]], list[httpx2.Request]]:
    bodies: list[dict[str, Any]] = []
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        bodies.append(json.loads(request.content))
        return httpx2.Response(status, json=reply or {})

    client = httpx2.AsyncClient(
        base_url="https://openrouter.ai/api/v1", transport=httpx2.MockTransport(answer)
    )
    search = PluginSearch(
        "sk-or-test",
        sites=SC.web_sites,
        game=SC.name,
        open_web=open_web,
        today=lambda: datetime.date(2026, 11, 22),
        client=client,
        fallback=fallback,
    )
    return search, bodies, requests


def _run(search: PluginSearch, query: object = "where is the admin office in Area18") -> Any:
    result = asyncio.run(search.run({"query": query}))
    asyncio.run(search.close())
    return result


def test_it_asks_haiku_on_anthropic_with_perplexity_search_and_keeps_no_data() -> None:
    search, bodies, requests = _search(_reply("In the IO-North tower lobby."))
    _run(search)
    body = bodies[0]
    assert body["model"] == MODEL
    assert body["plugins"][0]["id"] == "web"
    assert body["plugins"][0]["engine"] == ENGINE == "perplexity"
    assert "include_domains" not in body["plugins"][0]
    # Never a host that keeps data (#488), and Anthropic only, as for the brain (#313).
    assert body["provider"] == {
        "data_collection": "deny",
        "order": ["anthropic"],
        "allow_fallbacks": False,
    }
    assert body["messages"][0]["content"] == web.OPEN_BRIEF
    assert body["messages"][1]["content"] == (
        "[22 November 2026] where is the admin office in Area18 (Star Citizen)"
    )
    assert requests[0].headers["Authorization"] == "Bearer sk-or-test"


def test_with_the_open_web_off_it_searches_only_the_game_s_sites() -> None:
    search, bodies, _ = _search(_reply("Alpha 5.0 is next."), open_web=False)
    _run(search)
    assert bodies[0]["plugins"][0]["include_domains"] == list(SC.web_sites)
    assert bodies[0]["messages"][0]["content"] == web.BRIEF
    assert search.description == web.DESCRIPTION


def test_it_answers_as_the_same_tool_as_anthropic_s_search() -> None:
    search, _, _ = _search()
    real = web.WebSearch.__new__(web.WebSearch)
    assert (search.name, search.input_schema) == (real.name, real.input_schema)
    assert search.description == web.OPEN_DESCRIPTION
    assert search.from_the_web  # nothing is pressed after it (ADR 15)
    assert search.seconds < web.SECONDS


def test_the_answer_comes_back_with_its_sites() -> None:
    urls = ("https://starcitizen.tools/IO-North_tower", "https://starcitizen.tools/Area18")
    search, _, _ = _search(_reply("It's in the IO-North tower's lobby.", urls=(*urls, urls[0])))
    result = _run(search)
    assert not result.is_error
    found = json.loads(result.content)
    assert found["answer"] == "It's in the IO-North tower's lobby."
    assert found["sites"] == list(urls)
    assert found["source"] == "web search, any site"
    assert found["date"] == "2026-11-22"


def test_links_and_marks_are_never_read_aloud() -> None:
    # The plugin's own lead-in asks for markdown links; the model sometimes still writes
    # them, and bold, which a voice reads as noise.
    said = spoken_plainly(
        "The **Admin Office** is in [IO-North](https://starcitizen.tools/IO-North_tower) "
        "(https://example.com/a) on the ground floor."
    )
    assert said == "The Admin Office is in IO-North on the ground floor."


def test_a_cut_off_answer_keeps_whole_sentences() -> None:
    assert spoken_plainly("It's at Levski. Pay at the admi", "length") == "It's at Levski."


@pytest.mark.parametrize("status", [401, 402, 500])
def test_a_refused_request_is_a_failure_to_answer_without(status: int) -> None:
    search, _, _ = _search({"error": {"message": "no"}}, status=status)
    result = _run(search)
    assert result.is_error
    assert "answer without it" in json.loads(result.content)["error"]


def test_an_empty_answer_is_nothing_usable() -> None:
    search, _, _ = _search(_reply(""))
    result = _run(search)
    assert result.is_error
    assert "nothing usable" in json.loads(result.content)["error"]


def test_no_query_asks_for_one_without_a_request() -> None:
    search, bodies, _ = _search(_reply("x"))
    assert _run(search, "  ").is_error
    assert bodies == []


def test_a_bare_link_at_a_sentence_end_leaves_a_whole_sentence() -> None:
    """#551: the full stop after a link stays, and a "see" or "at" before it goes."""
    assert spoken_plainly("It's at Levski, see https://x.example/a.") == "It's at Levski."
    assert spoken_plainly("Docks at https://x.example/a. Then pay.") == "Docks. Then pay."
    assert spoken_plainly("Prices: https://uex.example/?q=1, and more.") == "Prices, and more."


class _Anthropic:
    """Stands in for Anthropic's search behind a refused OpenRouter key."""

    seconds = 18.0

    def __init__(self) -> None:
        self.asked: list[Any] = []
        self.closed = False

    async def run(self, arguments: Any) -> Any:
        from tycho.brain.lookups import LookupResult

        self.asked.append(arguments)
        return LookupResult('{"answer": "From Anthropic."}')

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [401, 402, 403])
def test_a_refused_openrouter_key_searches_with_the_anthropic_key_from_then_on(
    status: int,
) -> None:
    """#551: an old OpenRouter key, revoked or out of credit, mustn't stop a player whose
    Anthropic key works from searching."""
    anthropic = _Anthropic()
    search, bodies, _ = _search({"error": {"message": "no"}}, status=status, fallback=anthropic)
    assert search.seconds < anthropic.seconds

    async def twice() -> list[Any]:
        found = [await search.run({"query": q}) for q in ("one", "two")]
        await search.close()
        return found

    first, second = asyncio.run(twice())
    assert not first.is_error
    assert not second.is_error
    assert len(anthropic.asked) == 2
    assert len(bodies) == 1  # OpenRouter isn't asked again
    assert search.seconds == anthropic.seconds
    assert anthropic.closed


def test_a_server_error_isnt_a_refused_key() -> None:
    anthropic = _Anthropic()
    search, _, _ = _search({"error": {"message": "down"}}, status=500, fallback=anthropic)
    assert _run(search).is_error
    assert anthropic.asked == []
