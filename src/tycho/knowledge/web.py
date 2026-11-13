"""Web search as a look-up (ADR 15): a separate, short Claude request that searches
Star Citizen sites and sums up what it found.

Offering Anthropic's search tool in the brain's own request made every command
about 0.3 s slower, searched or not. As a look-up, the brain's request carries
only a small tool like any other, and the search runs in a request of its own,
with its own brief: short, in its own words, official word first.

What comes back can't be checked, so the brain may not press anything after it
(``from_the_web``), and it takes longer than other look-ups (``seconds``).

``web_search_open`` (#354, on by default since 13 Nov 2026) adds a fallback: when the
sites have no clear answer, a second request may use the whole web and read up to two
of the pages it finds (Anthropic's web fetch), for "where is" and "how do I" answers
that live in community guides.
"""

import asyncio
import dataclasses
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
"""The sites a search asks first, and the only ones without the fallback: the game
pack's (``[web]`` in its pack.toml). For Star Citizen, RSI's own, the community wiki and
the community tools (2026-11-02): the open web alone brought fan sites and leaked dates."""
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

FALLBACK_TAG = re.compile(r"^\W*(FOUND|PARTIAL|NONE)\b\W*")
"""How each search of a fallback says how well it did: a whole word in capitals, read
only from a request that asked for it ("None of RSI's posts…" is an answer)."""
FALLBACK_TAG_LAST = re.compile(r"\s*[(\[]?\b(FOUND|PARTIAL|NONE)\b[)\]]?[.!]?\s*$")
"""The same tag put last instead ("… at Levski. PARTIAL"): never read aloud (#373)."""
RANK = {"NONE": 0, "PARTIAL": 1, "FOUND": 2}
"""How the two answers of a fallback compare: the open web's replaces the sites' only
when it did at least as well by its own tag (#372 benchmark: a PARTIAL from the sites
became "I couldn't find it")."""
WIDER_MARGIN = 1.0
"""Seconds kept back from ``OPEN_SECONDS`` so a slow open-web step gives up in time to
return the sites' answer, not a timeout (#373)."""
FALLBACK_ASK = """

Start your reply with one word in capitals, then your answer: FOUND if the results \
answer the question clearly, PARTIAL if they only answer part of it or are unsure, \
NONE if they don't answer it."""
"""Added to ``BRIEF`` for the first search of a fallback (#354)."""

FALLBACK_BRIEF = """\
You search the web for a voice co-pilot in Star Citizen, who will read your answer \
aloud to a player who is flying. The game's own sites (RSI, the community wiki, the \
community tools) didn't answer this clearly, so look further: community guides, \
forum and Reddit threads, blogs and videos often have where things are, how to do \
things and what players think. What the game's sites said, if anything, follows the \
question: keep what's right in it and add what's missing. Search, then read the most \
useful page before you answer, unless the snippets already answer it fully. Use only \
sources about Star Citizen, and ignore anything else the search returns. Guides go out \
of date with each patch: when a guide or forum post disagrees with RSI's patch notes \
or the community wiki, trust the patch notes and the wiki, and prefer sources from the \
last six months (today's date starts the question). Say only names, numbers and \
places you saw in a source. Then answer in one to three short plain sentences, in \
your own words: no quotes, links, lists or markdown. When the answer comes from \
players rather than RSI, say so in a few words ("players report"). Leave out leaks \
and fan estimates unless the query asks for rumours. If you find nothing relevant, \
say only that, in a few words. Never suggest checking a website, channel or source: \
the player can't, they're flying."""
"""The open-web step of a fallback: it only runs when the game's sites fell short, so
it's told to read a page (unprompted, it read none in the 12 Nov benchmark), and to
trust the wiki and patch notes over guides (the same benchmark: old guides' claims,
said with confidence, were most of its wrong answers)."""

NO_PAGE_READS = ("reddit.com",)
"""Sites whose pages are never read, only their search snippets: Reddit's terms restrict
automated access (knowledge/AGENTS.md: never scrape a site whose terms forbid it)."""

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
    _fallback = False
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
        fallback: bool = False,
    ) -> None:
        """``log_queries``: put the query (the player's question, reworded) in the log
        file. Only with ``keep_transcripts`` (audit, 2026-11-03). ``open_web``: any site
        and page reads, every time (the benchmark's "open"). ``fallback``: the game's
        sites first, then the open web only when they had no clear answer
        (``web_search_open``, #354)."""
        self._client = client
        self._model = model
        self._today = today
        self._log_queries = log_queries
        self._sites = tuple(sites)
        """The game pack's sites: the only ones a search may read, unless ``open_web``."""
        self._open = open_web
        self._fallback = fallback and not open_web
        self.seconds = OPEN_SECONDS if open_web or self._fallback else SECONDS

    @property
    def name(self) -> str:
        return NAME

    @property
    def description(self) -> str:
        return OPEN_DESCRIPTION if self._open or self._fallback else DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return SCHEMA

    async def prepare(self) -> None:
        return None

    def _tools(self, open_web: bool) -> list[dict[str, object]]:
        if not open_web:
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
                "blocked_domains": list(NO_PAGE_READS),
            },
        ]

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return failure("pass query: a short search query")
        today = self._today()
        asked = f"[{today.day} {today:%B %Y}] {query}"
        started = time.perf_counter()
        try:
            if not self._fallback:
                found = await self._search(
                    query, asked, open_web=self._open, brief=OPEN_BRIEF if self._open else BRIEF
                )
                return self._result(query, today, found, self._open)
            first = await self._search(
                query, asked, open_web=False, brief=BRIEF + FALLBACK_ASK, tagged=True
            )
        except anthropic.APIError as e:
            log.warning("Web search failed: %s", e)
            return failure(f"the web search failed ({type(e).__name__}); answer without it")
        if first.verdict == "FOUND" and first.answer:
            return self._result(query, today, first, False)
        wider = await self._wider(query, asked, first, started)
        if wider is None or not wider.answer or RANK[wider.verdict] < RANK[first.verdict]:
            return self._result(query, today, first, False)
        return self._result(query, today, wider, True)

    async def _wider(self, query: str, asked: str, first: _Found, started: float) -> _Found | None:
        """The open-web step of a fallback, given what the sites said; ``None`` if it
        failed or ran out of time, so the sites' answer, if any, still stands (#373)."""
        left = self.seconds - WIDER_MARGIN - (time.perf_counter() - started)
        log.debug("Web search: the game's sites said %s; trying the open web", first.verdict)
        if first.answer:
            asked = f"{asked}\n\nWhat the game's sites said: {first.answer}"
        try:
            return await asyncio.wait_for(
                self._search(
                    query, asked, open_web=True, brief=FALLBACK_BRIEF + FALLBACK_ASK, tagged=True
                ),
                max(left, 0.0),
            )
        except (TimeoutError, anthropic.APIError) as e:
            log.warning("Web search of the open web failed: %s", type(e).__name__)
            return None

    def _result(
        self, query: str, today: datetime.date, found: _Found, open_web: bool
    ) -> LookupResult:
        if not found.answer:
            return failure("the search found nothing usable; answer without it")
        result = {
            "source": "web search, any site" if open_web else "web search of Star Citizen sites",
            "searched": query,
            "date": today.isoformat(),
            "answer": found.answer,
            "sites": found.sites[:3],
        }
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    async def _search(
        self, query: str, asked: str, *, open_web: bool, brief: str, tagged: bool = False
    ) -> _Found:
        started = time.perf_counter()
        messages: list[dict[str, Any]] = [{"role": "user", "content": asked}]
        content: list[Any] = []
        spent = [0, 0]
        stopped = ""
        # A long run of server tools can pause the turn; the open web may go on once.
        for _ in range(2 if open_web else 1):
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=brief,
                tools=self._tools(open_web),  # pyright: ignore[reportArgumentType]
                output_config={"effort": "low"},
                messages=messages,  # pyright: ignore[reportArgumentType]
            )
            content += message.content
            spent[0] += message.usage.input_tokens
            spent[1] += message.usage.output_tokens
            stopped = str(getattr(message, "stop_reason", "") or "")
            if stopped != "pause_turn":
                break
            messages = [*messages, {"role": "assistant", "content": message.content}]
        answer = _answer(content, cut=stopped == "max_tokens")
        verdict = "FOUND"
        if tagged:
            verdict, answer = _verdict(answer)
        sites = _sites(content)
        searches, pages = _uses(content)
        cost = (spent[0] * PRICES[0] + spent[1] * PRICES[1]) / 1_000_000
        log.debug(
            "Web search%s%s in %.0f ms: %d in, %d out, %d searches, %d pages read, %d sites, "
            "about $%.5f, stopped: %s",
            " (open web)" if open_web else "",
            f" {query!r}" if self._log_queries else "",
            (time.perf_counter() - started) * 1000,
            spent[0],
            spent[1],
            searches,
            pages,
            len(sites),
            cost + SEARCH_PRICE * max(searches, 1),
            stopped,
        )
        return _Found(answer, sites, verdict)


@dataclasses.dataclass(frozen=True)
class _Found:
    """One search request's answer, its sites, and (in a fallback's first search) how
    well it did: FOUND, PARTIAL or NONE."""

    answer: str
    sites: list[str]
    verdict: str


_SPACE_BEFORE = re.compile(r"\s+([,.;:!?])")
_SENTENCE_END = re.compile(r"[.!?](?=\s+[A-Z\"']|$)")
"""A full stop that ends a sentence: the next word starts with a capital, or the text
ends. Not "Dec. 12" (#368)."""


def _answer(content: Sequence[Any], *, cut: bool) -> str:
    """The text after the last search or page read, on one line, at most ``MAX_ANSWER``
    long: anything before it is the model saying what it will look up, not the answer.
    Cited text comes in several blocks that split mid-sentence, so they're joined as they
    are (#366: "enter , and"). ``cut``:
    the reply hit ``MAX_TOKENS``, so keep only whole sentences: a word cut in half, read
    aloud, sounds broken, and nothing whole means nothing usable."""
    last = max(
        (i for i, b in enumerate(content) if str(getattr(b, "type", "")).endswith("_tool_result")),
        default=-1,
    )
    text = "".join(str(b.text) for b in content[last + 1 :] if getattr(b, "type", "") == "text")
    text = _SPACE_BEFORE.sub(r"\1", " ".join(text.split()))
    if cut:
        text = _whole_sentences(text)
    if len(text) > MAX_ANSWER:
        text = _whole_sentences(text[: MAX_ANSWER + 1]) or text[:MAX_ANSWER].rsplit(" ", 1)[0]
    return text


def _verdict(answer: str) -> tuple[str, str]:
    """A tagged answer's FOUND, PARTIAL or NONE, first or last, and the answer without
    it. With no tag, PARTIAL: it didn't say it found a clear answer."""
    if tag := FALLBACK_TAG.match(answer):
        verdict, answer = tag[1], answer[tag.end() :]
    elif tag := FALLBACK_TAG_LAST.search(answer):
        verdict, answer = tag[1], answer[: tag.start()]
    else:
        return "PARTIAL", answer
    return verdict, "" if verdict == "NONE" else answer


def _whole_sentences(text: str) -> str:
    """``text`` up to the end of its last whole sentence, or "" if none ends."""
    ends = [m.end() for m in _SENTENCE_END.finditer(text)]
    return text[: ends[-1]] if ends else ""


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
