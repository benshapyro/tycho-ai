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
import re
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
MAX_TOKENS = 2000
"""Room for the search request's whole reply. Haiku thinks before and after it
searches, and that counts too: with 400, half the answers stopped mid-word (#366). A
spoken answer is about 80 tokens, so the extra room costs nothing unless it's used."""

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
        sites: Sequence[str] = SITES,
    ) -> None:
        """``log_queries``: put the query (the player's question, reworded) in the log
        file. Only with ``keep_transcripts`` (audit, 2026-11-03)."""
        self._client = client
        self._model = model
        self._today = today
        self._log_queries = log_queries
        self._sites = tuple(sites)
        """The game pack's sites: the only ones a search may read."""

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
                max_tokens=MAX_TOKENS,
                system=BRIEF,
                tools=[
                    {
                        "type": WEB_SEARCH,
                        "name": "web_search",
                        "max_uses": 1,
                        "allowed_domains": list(self._sites),
                    }
                ],
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": f"[{today.day} {today:%B %Y}] {query}"}],
            )
        except anthropic.APIError as e:
            log.warning("Web search failed: %s", e)
            return failure(f"the web search failed ({type(e).__name__}); answer without it")
        stopped = str(getattr(message, "stop_reason", "") or "")
        answer = _answer(message.content, cut=stopped == "max_tokens")
        sites = _sites(message.content)
        usage = message.usage
        cost = (usage.input_tokens * PRICES[0] + usage.output_tokens * PRICES[1]) / 1_000_000
        log.debug(
            "Web search%s in %.0f ms: %d in, %d out, %d sites, about $%.5f, stopped: %s",
            f" {query!r}" if self._log_queries else "",
            (time.perf_counter() - started) * 1000,
            usage.input_tokens,
            usage.output_tokens,
            len(sites),
            cost + SEARCH_PRICE,
            stopped,
        )
        if not answer:
            return failure("the search found nothing usable; answer without it")
        result = {
            "source": "web search of Star Citizen sites",
            "searched": query,
            "date": today.isoformat(),
            "answer": answer,
            "sites": sites[:3],
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


_SPACE_BEFORE = re.compile(r"\s+([,.;:!?])")
_SENTENCE_END = re.compile(r"[.!?](?=\s+[A-Z\"']|$)")
"""A full stop that ends a sentence: the next word starts with a capital, or the text
ends. Not "Dec. 12" (#368)."""


def _answer(content: Sequence[Any], *, cut: bool) -> str:
    """The text, on one line, at most ``MAX_ANSWER`` long. Cited text comes in several
    blocks that split mid-sentence, so text blocks next to each other are joined as they
    are (#366: "enter , and"), while a search between two keeps a space (#368). ``cut``:
    the reply hit ``MAX_TOKENS``, so keep only whole sentences: a word cut in half, read
    aloud, sounds broken, and nothing whole means nothing usable."""
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", "") == "text":
            parts.append(str(block.text))
        elif parts:
            parts.append(" ")
    text = _SPACE_BEFORE.sub(r"\1", " ".join("".join(parts).split()))
    if cut:
        text = _whole_sentences(text)
    if len(text) > MAX_ANSWER:
        text = _whole_sentences(text[: MAX_ANSWER + 1]) or text[:MAX_ANSWER].rsplit(" ", 1)[0]
    return text


def _whole_sentences(text: str) -> str:
    """``text`` up to the end of its last whole sentence, or "" if none ends."""
    ends = [m.end() for m in _SENTENCE_END.finditer(text)]
    return text[: ends[-1]] if ends else ""


def _sites(content: Any) -> list[str]:
    """Where the results came from, by site and page, for the log and for Claude."""
    sites: list[str] = []
    for block in content:
        if getattr(block, "type", "") == "web_search_tool_result" and isinstance(
            results := getattr(block, "content", None), list
        ):
            sites += [str(getattr(r, "url", "")) for r in results if getattr(r, "url", "")]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return sites
