"""Web search through OpenRouter's web plugin, on Perplexity's search (ADR 15, #365).

One request: OpenRouter searches with the question, puts the results in the request,
and Haiku answers from them with the same brief as the open web's step in ``web``. In
the #365 benchmark (40 questions, two rounds) it answered in about 1.5 s instead of
10, for about half a cent instead of 3, with at least as many right answers and no
more wrong ones. It runs on the player's OpenRouter key, so a player with only that
key can search too (#112).

Like ``web.WebSearch``, what comes back can't be checked: nothing is pressed after it
(``from_the_web``).
"""

import datetime
import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import httpx2

from tycho.brain.lookups import LookupResult, failure
from tycho.knowledge import web

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1"
MODEL = "anthropic/claude-haiku-5.5"
HOSTS = ("anthropic",)
"""Who runs the model: Anthropic only, as for the player's OpenRouter brain (#313)."""
ENGINE = "perplexity"
"""The search behind OpenRouter's web plugin. Perplexity was the fastest in #365, with
the fewest wrong answers; Firecrawl is refused under zero data retention."""
RESULTS = 5
SECONDS = 8.0
"""How long the search may take: 1.5 s typical, 4.1 s at the slowest in #365."""
SEARCH_PROMPT = (
    "Web search results for the player's question follow. Answer from them as your brief "
    "says: never cite, link or name a URL, because the answer is read aloud."
)
"""Replaces the plugin's own lead-in, which asks for markdown links: read aloud, a link
is noise."""

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"\(?https?://\S+\)?")
_MARKS = re.compile(r"\*\*|__|`|^#+\s*", re.MULTILINE)


class PluginSearch:
    """Satisfies ``brain.lookups.Lookup``: the same name and input as ``web.WebSearch``,
    so the brain's tools don't change with the search behind them."""

    from_the_web = True
    """Its results can't be checked: nothing may be pressed after it (ADR 15)."""
    seconds = SECONDS

    def __init__(
        self,
        api_key: str,
        *,
        sites: Sequence[str],
        game: str,
        open_web: bool = True,
        today: Callable[[], datetime.date] = datetime.date.today,
        log_queries: bool = False,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        """``sites``: the game pack's, the only ones searched when ``open_web`` is off
        (``web_search_open = false``). ``game``: the pack's name, added to the query so the
        search finds that game's pages. ``log_queries``: put the query in the log file,
        only with ``keep_transcripts``."""
        self._sites = tuple(sites)
        self._game = game
        self._open = open_web
        self._today = today
        self._log_queries = log_queries
        self._http = client or httpx2.AsyncClient(
            base_url=URL, timeout=httpx2.Timeout(SECONDS, connect=3.0)
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Title": "Tycho",
            "HTTP-Referer": "https://github.com/benshapyro/tycho-ai",
        }

    @property
    def name(self) -> str:
        return web.NAME

    @property
    def description(self) -> str:
        return web.OPEN_DESCRIPTION if self._open else web.DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return web.SCHEMA

    async def prepare(self) -> None:
        return None

    async def close(self) -> None:
        await self._http.aclose()

    def body(self, query: str) -> dict[str, object]:
        """The request: Haiku on Anthropic only, never a host that keeps data (#488), with
        the web plugin on ``ENGINE``."""
        today = self._today()
        plugin: dict[str, object] = {
            "id": "web",
            "engine": ENGINE,
            "max_results": RESULTS,
            "search_prompt": SEARCH_PROMPT,
        }
        if not self._open:
            plugin["include_domains"] = list(self._sites)
        return {
            "model": MODEL,
            "max_tokens": web.MAX_TOKENS,
            "messages": [
                {"role": "system", "content": web.OPEN_BRIEF if self._open else web.BRIEF},
                {"role": "user", "content": f"[{today.day} {today:%B %Y}] {query} ({self._game})"},
            ],
            "plugins": [plugin],
            "provider": {"data_collection": "deny", "order": list(HOSTS), "allow_fallbacks": False},
            "reasoning": {"effort": "low"},
            "usage": {"include": True},
        }

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return failure("pass query: a short search query")
        started = time.perf_counter()
        try:
            response = await self._http.post(
                "/chat/completions", json=self.body(query), headers=self._headers
            )
        except httpx2.HTTPError as e:
            log.warning("Web search failed: %s", type(e).__name__)
            return failure(f"the web search failed ({type(e).__name__}); answer without it")
        if response.status_code != 200:
            log.warning("Web search failed: OpenRouter returned %d", response.status_code)
            return failure("the web search failed; answer without it")
        reply = cast("dict[str, Any]", response.json())
        choice = cast("dict[str, Any]", (reply.get("choices") or [{}])[0])
        message = cast("dict[str, Any]", choice.get("message") or {})
        answer = spoken_plainly(str(message.get("content") or ""), choice.get("finish_reason"))
        sites = _sites(message.get("annotations"))
        usage = cast("dict[str, Any]", reply.get("usage") or {})
        log.debug(
            "Web search (%s)%s in %.0f ms: %d sites, $%.5f, stopped: %s",
            ENGINE,
            f" {query!r}" if self._log_queries else "",
            (time.perf_counter() - started) * 1000,
            len(sites),
            float(usage.get("cost") or 0.0),
            choice.get("finish_reason"),
        )
        if not answer:
            return failure("the search found nothing usable; answer without it")
        result = {
            "source": "web search, any site" if self._open else f"web search of {self._game} sites",
            "searched": query,
            "date": self._today().isoformat(),
            "answer": answer,
            "sites": sites[:3],
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def spoken_plainly(text: str, finish_reason: object = None) -> str:
    """The answer as it will be read aloud: no markdown links, bare links or marks, which
    the model sometimes writes despite its brief, in whole sentences if it was cut off."""
    text = _URL.sub("", _LINK.sub(r"\1", text))
    text = _MARKS.sub("", text)
    return web.spoken(text, cut=finish_reason == "length")


def _sites(annotations: object) -> list[str]:
    """The pages the answer cites, in order, each once."""
    found: list[str] = []
    for note in cast("list[dict[str, Any]]", annotations if isinstance(annotations, list) else []):
        cited = cast("dict[str, Any]", (note or {}).get("url_citation") or {})
        url = str(cited.get("url") or "")
        if url and url not in found:
            found.append(url)
    return found
