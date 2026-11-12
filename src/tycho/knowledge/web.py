"""Web search as a look-up (ADR 15): a separate, short Claude request that searches
Star Citizen sites and sums up what it found.

Offering Anthropic's search tool in the brain's own request made every command
about 0.3 s slower, searched or not. As a look-up, the brain's request carries
only a small tool like any other, and the search runs in a request of its own,
with its own brief: short, in its own words, official word first.

What comes back can't be checked, so the brain may not press anything after it
(``from_the_web``), and it takes longer than other look-ups (``seconds``).

``web_search_open`` (a trial for #354, off by default) lifts the site list: the search
may use the whole web, and it may read up to two of the pages it finds (Anthropic's
web fetch), for "where is" and "how do I" answers that live in community guides.
"""

import datetime
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import anthropic

from tycho.brain.lookups import LookupResult, failure
from tycho.packs import built_in

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
WEB_SEARCH = "web_search_20250305"
"""Haiku 5.5 has the version without dynamic filtering."""
SITES = built_in().web_sites
"""The only sites a search may read: the game pack's (``[web]`` in its pack.toml). For
Star Citizen, RSI's own, the community wiki and the community tools (2026-11-02): the
open web brought fan sites and leaked dates."""
PRICES = (0.10, 0.50)
"""Haiku 5.5 per million tokens, input and output."""
SEARCH_PRICE = 0.01
SECONDS = 12.0
"""How long the search request may take; searches take 4 to 8 s."""
MAX_ANSWER = 600

WEB_FETCH = "web_fetch_20250910"
"""The version Haiku 5.5 has, without dynamic filtering; no beta header."""
OPEN_USES = 2
"""With the open web: up to two searches and two page reads a request."""
OPEN_PAGE_TOKENS = 6000
"""How much of a page a read may bring back. At Haiku's price, two full pages cost
about a tenth of a cent."""
OPEN_SECONDS = 25.0
"""Searching twice and reading pages takes longer: about 8 to 20 s (an estimate until
the trial measures it)."""

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

OPEN_DESCRIPTION = """\
Search the web for Star Citizen: news, upcoming patches, events, new ships, and also \
where something is and how to do something in the game (locations, terminals, \
missions, mechanics), from official sites and community guides, wikis and forums. Use \
it on the first ask whenever another look-up can't answer and you don't know for sure: \
never say you can't look something up. Slow (several seconds) and costs money. Pass \
one short search query."""

OPEN_BRIEF = """\
You search the web for a voice co-pilot in Star Citizen, who will read your answer \
aloud to a player who is flying. Search, and read a page from the results when the \
snippets don't give the answer (community guides, wikis and forum posts often have \
where things are and how to do things). Then answer in one to three short plain \
sentences, in your own words: no quotes, links, lists or markdown. Lead with what \
RSI has officially said, with dates, when there is official word. When the answer \
comes from players rather than RSI, say so in a few words ("players report") and, \
if the post is old or the game may have changed since, say that too. Leave out \
leaks and fan estimates unless the query asks for rumours. If you find nothing \
relevant, say only that, in a few words. Never suggest checking a website, channel \
or source: the player can't, they're flying."""

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
    _open = False
    seconds = SECONDS

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str = MODEL,
        today: Callable[[], datetime.date] = datetime.date.today,
        log_queries: bool = False,
        sites: Sequence[str] = SITES,
        open_web: bool = False,
    ) -> None:
        """``log_queries``: put the query (the player's question, reworded) in the log
        file. Only with ``keep_transcripts`` (audit, 2026-11-03). ``open_web``: the
        #354 trial, any site and page reads (``web_search_open``)."""
        self._client = client
        self._model = model
        self._today = today
        self._log_queries = log_queries
        self._sites = tuple(sites)
        """The game pack's sites: the only ones a search may read, unless ``open_web``."""
        self._open = open_web
        self.seconds = OPEN_SECONDS if open_web else SECONDS

    @property
    def name(self) -> str:
        return NAME

    @property
    def description(self) -> str:
        return OPEN_DESCRIPTION if self._open else DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return SCHEMA

    async def prepare(self) -> None:
        return None

    def _tools(self) -> list[dict[str, object]]:
        if not self._open:
            return [
                {
                    "type": WEB_SEARCH,
                    "name": "web_search",
                    "max_uses": 1,
                    "allowed_domains": list(self._sites),
                }
            ]
        return [
            {"type": WEB_SEARCH, "name": "web_search", "max_uses": OPEN_USES},
            {
                "type": WEB_FETCH,
                "name": "web_fetch",
                "max_uses": OPEN_USES,
                "max_content_tokens": OPEN_PAGE_TOKENS,
            },
        ]

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return failure("pass query: a short search query")
        started = time.perf_counter()
        today = self._today()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"[{today.day} {today:%B %Y}] {query}"}
        ]
        content: list[Any] = []
        spent = [0, 0]
        try:
            # A long run of server tools can pause the turn; the open web may go on once.
            for _ in range(2 if self._open else 1):
                message = await self._client.messages.create(
                    model=self._model,
                    max_tokens=400,
                    system=OPEN_BRIEF if self._open else BRIEF,
                    tools=self._tools(),  # pyright: ignore[reportArgumentType]
                    output_config={"effort": "low"},
                    messages=messages,  # pyright: ignore[reportArgumentType]
                )
                content += message.content
                spent[0] += message.usage.input_tokens
                spent[1] += message.usage.output_tokens
                if getattr(message, "stop_reason", None) != "pause_turn":
                    break
                messages = [*messages, {"role": "assistant", "content": message.content}]
        except anthropic.APIError as e:
            log.warning("Web search failed: %s", e)
            return failure(f"the web search failed ({type(e).__name__}); answer without it")
        answer = _answer(content)
        sites = _sites(content)
        searches, pages = _uses(content)
        cost = (spent[0] * PRICES[0] + spent[1] * PRICES[1]) / 1_000_000
        log.debug(
            "Web search%s%s in %.0f ms: %d in, %d out, %d searches, %d pages read, %d sites, "
            "about $%.5f",
            " (open web)" if self._open else "",
            f" {query!r}" if self._log_queries else "",
            (time.perf_counter() - started) * 1000,
            spent[0],
            spent[1],
            searches,
            pages,
            len(sites),
            cost + SEARCH_PRICE * max(searches, 1),
        )
        if not answer:
            return failure("the search found nothing usable; answer without it")
        result = {
            "source": "web search, any site (trial)"
            if self._open
            else "web search of Star Citizen sites",
            "searched": query,
            "date": today.isoformat(),
            "answer": answer[:MAX_ANSWER],
            "sites": sites[:3],
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def _answer(content: Sequence[Any]) -> str:
    """The text after the last search or page read, on one line: anything before it
    is the model saying what it will look up, not the answer."""
    last = max(
        (i for i, b in enumerate(content) if str(getattr(b, "type", "")).endswith("_tool_result")),
        default=-1,
    )
    text = " ".join(b.text for b in content[last + 1 :] if getattr(b, "type", "") == "text")
    return " ".join(text.split())


def _sites(content: Any) -> list[str]:
    """Where the results came from, by site and page, for the log and for Claude: pages
    read first, then search results."""
    read: list[str] = []
    found: list[str] = []
    for block in content:
        kind = getattr(block, "type", "")
        results = getattr(block, "content", None)
        if kind == "web_search_tool_result" and isinstance(results, list):
            found += [str(getattr(r, "url", "")) for r in results if getattr(r, "url", "")]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        elif kind == "web_fetch_tool_result" and getattr(results, "url", ""):
            read.append(str(getattr(results, "url", "")))
    return read + [url for url in found if url not in read]


def _uses(content: Any) -> tuple[int, int]:
    """How many searches ran and pages were read: a search costs 1 cent each."""
    names = [getattr(b, "name", "") for b in content if getattr(b, "type", "") == "server_tool_use"]
    return names.count("web_search"), names.count("web_fetch")
