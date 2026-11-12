"""Web search as a look-up (ADR 15): a separate, short Claude request that searches
Star Citizen sites and sums up what it found.

Offering Anthropic's search tool in the brain's own request made every command
about 0.3 s slower, searched or not. As a look-up, the brain's request carries
only a small tool like any other, and the search runs in a request of its own,
with its own brief: short, in its own words, official word first.

What comes back can't be checked, so the brain may not press anything after it
(``from_the_web``), and it takes longer than other look-ups (``seconds``).
"""

import datetime
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import anthropic

from tycho.brain.lookups import LookupResult, failure

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
WEB_SEARCH = "web_search_20250305"
"""Haiku 5.5 has the version without dynamic filtering."""
SITES = (
    "robertsspaceindustries.com",
    "starcitizen.tools",
    "uexcorp.space",
    "erkul.games",
    "sc-craft.tools",
    "scmdb.net",
)
"""The only sites a search may read (2026-11-02): RSI's own (patch notes, roadmap,
Comm-Links, Spectrum, support), the community wiki and the community tools. The
open web brought fan sites and leaked dates."""
PRICES = (0.10, 0.50)
"""Haiku 5.5 per million tokens, input and output."""
SEARCH_PRICE = 0.01
SECONDS = 12.0
"""How long the search request may take; searches take 4 to 8 s."""
MAX_ANSWER = 600

DESCRIPTION = """\
Search Star Citizen websites (RSI's patch notes, roadmap, Comm-Links and forums, \
the community wiki, UEX) for what changes: news, upcoming patches and what's in \
them, release dates, events and free flies, newly revealed ships, the roadmap. \
Slow (several seconds) and costs money: don't use it for anything another look-up \
covers or that you know well. Pass one short search query."""

BRIEF = """\
You search Star Citizen websites for a voice co-pilot, who will read your answer \
aloud to a player who is flying. Search once, then answer in one or two short \
plain sentences, in your own words: no quotes, links, lists or markdown. Lead with \
what RSI has officially said, with dates; if there's no official word, say so. \
Leave out leaks and fan estimates unless the query asks for rumours. If the \
search finds nothing relevant, say only that, in a few words. Never suggest \
checking a website, channel or source: the player can't, they're flying."""

NAME = "web_search"
SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": 'A short search query, e.g. "Star Citizen 4.11 patch".',
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}
NEEDS_ANTHROPIC = (
    "Web search needs an Anthropic key, and the player has only an OpenRouter key. Tell "
    "them in one short sentence that you can't search the web without one, then answer from "
    "what you know if you can, saying it may be out of date."
)


class SearchNeedsAnthropic:
    """``web_search`` for a brain through OpenRouter when the player has no Anthropic
    key (#112). Search is Anthropic's own tool, so the brain is told plainly why it can't
    search, and says so, rather than the question failing quietly. The same name,
    description and schema as ``WebSearch``, so the tools don't change.
    Satisfies ``brain.lookups.Lookup``."""

    instant = True
    """It answers at once: no "Checking." first."""

    @property
    def name(self) -> str:
        return NAME

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return SCHEMA

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        # Not an error result: an error makes the model try again (brain/AGENTS.md).
        return LookupResult(json.dumps({"not_available": NEEDS_ANTHROPIC}))


class WebSearch:
    """Satisfies ``brain.lookups.Lookup``."""

    from_the_web = True
    """Its results can't be checked: nothing may be pressed after it (ADR 15)."""
    seconds = SECONDS

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str = MODEL,
        today: Callable[[], datetime.date] = datetime.date.today,
        log_queries: bool = False,
    ) -> None:
        """``log_queries``: put the query (the player's question, reworded) in the log
        file. Only with ``keep_transcripts`` (audit, 2026-11-03)."""
        self._client = client
        self._model = model
        self._today = today
        self._log_queries = log_queries

    @property
    def name(self) -> str:
        return NAME

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return SCHEMA

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return failure("pass query: a short search query")
        started = time.perf_counter()
        today = self._today()
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=400,
                system=BRIEF,
                tools=[
                    {
                        "type": WEB_SEARCH,
                        "name": "web_search",
                        "max_uses": 1,
                        "allowed_domains": list(SITES),
                    }
                ],
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"[{today.day} {today:%B %Y}] {query}"}],
            )
        except anthropic.APIError as e:
            log.warning("Web search failed: %s", e)
            return failure(f"the web search failed ({type(e).__name__}); answer without it")
        answer = " ".join(" ".join(b.text for b in message.content if b.type == "text").split())
        sites = _sites(message.content)
        usage = message.usage
        cost = (usage.input_tokens * PRICES[0] + usage.output_tokens * PRICES[1]) / 1_000_000
        log.debug(
            "Web search%s in %.0f ms: %d in, %d out, %d sites, about $%.5f",
            f" {query!r}" if self._log_queries else "",
            (time.perf_counter() - started) * 1000,
            usage.input_tokens,
            usage.output_tokens,
            len(sites),
            cost + SEARCH_PRICE,
        )
        if not answer:
            return failure("the search found nothing usable; answer without it")
        result = {
            "source": "web search of Star Citizen sites",
            "searched": query,
            "date": today.isoformat(),
            "answer": answer[:MAX_ANSWER],
            "sites": sites[:3],
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def _sites(content: Any) -> list[str]:
    """Where the results came from, by site and page, for the log and for Claude."""
    sites: list[str] = []
    for block in content:
        if getattr(block, "type", "") == "web_search_tool_result" and isinstance(
            results := getattr(block, "content", None), list
        ):
            sites += [str(getattr(r, "url", "")) for r in results if getattr(r, "url", "")]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return sites
