"""The brain on Claude Haiku 5.5 (ADR 6, ADR 14). Satisfies ``runtime.Brain``.

Speed matters most (#21): Tycho acts as soon as an act's tool call is complete,
and stops reading the stream there, so it never waits for the rest of the answer.
A reply can hold several tool calls (#149, #156); only its first act is pressed.

Look-ups (ADR 15) run in a loop: a look-up starts the moment its call is complete,
its result goes back to Claude, and Claude carries on, at most ``MAX_CALLS`` times.
An act ends the request, after waiting up to ``INSTANT_SECONDS`` for a fleet or
settings note from the same reply. Every call sends the same tools and settings, so
the cache holds across the loop and across requests.

A look-up from the web (``from_the_web``, the web search) is slow and can't be
checked: when it starts, ``BrainRequest.waiting`` is called so the player hears
a filler line, and after it the request may not press anything (ADR 15).

The briefing (about 11,000 tokens) is cached for an hour, because play is bursty:
gaps of 5 to 60 minutes are normal, and a cold cache added about 3 s. One warm-up
request at startup writes it, so the first real request is fast too.
"""

import asyncio
import dataclasses
import datetime
import difflib
import logging
import re
import time
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic.types import MessageParam, TextBlockParam, ToolParam, ToolResultBlockParam

from tycho.brain.briefing import briefing, offered_actions
from tycho.brain.decisions import (
    BrainError,
    BrainRequest,
    Decision,
    Exchange,
    Reply,
    UnboundAction,
    parse_tool_call,
)
from tycho.brain.lookups import LOOKUP_SECONDS, Lookup, LookupResult, failure, run_lookup
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Keybinds
from tycho.tile.cards import TOOL_DESCRIPTION, TOOL_SCHEMA

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
EFFORT = "low"
"""97% on the 82-case eval at about 1.2 s; medium scored the same, slower (ADR 6)."""
MAX_TOKENS = 1024
"""Replies are a sentence or two and tool calls a few dozen tokens; this is headroom."""
MAX_CALLS = 3
"""Calls to Claude per request: up to two rounds of look-ups, then the answer (ADR 15)."""
# Haiku 5.5 per million tokens: input, output, cache read, cache write (1 hour).
_PRICES = (0.10, 0.50, 0.01, 0.20)
NO_PRESS_AFTER_SEARCH = (
    "I looked that up online, so I won't press anything for it. Say the command again."
)


@dataclass
class _Turn:
    """What one request has done so far, across its calls to Claude."""

    waiting: Callable[[], None] | None = None
    looking: Callable[[], None] | None = None
    searched: bool = False
    credits: list[str] = field(default_factory=list[str])
    """Sources an answer must name, from the look-ups whose results it got ("UEX", #164)."""
    sources: dict[str, str] = field(default_factory=dict[str, str])
    """Each look-up's source, by its tool call's id, until its result is in."""
    unbound: UnboundAction | None = None
    """The last act refused for naming unbound actions, for the final error (#167)."""
    refused: bool = False
    """This call's reply held a refused act: its result asks for another pick."""


SHOW_CARD: ToolParam = {
    "name": "show_card",
    "description": TOOL_DESCRIPTION,
    "strict": True,
    "input_schema": dict(TOOL_SCHEMA),
}
"""Offered only while the Tile is on, so Claude never says "on the Tile" without one."""
ACTS = frozenset({"run_actions", "run_sequence", "show_card"})


def _tools(sequences: list[str], lookups: Iterable[Lookup] = ()) -> list[ToolParam]:
    looks: list[ToolParam] = [
        {
            "name": lookup.name,
            "description": lookup.description,
            "strict": True,
            "input_schema": dict(lookup.input_schema),
        }
        for lookup in lookups
    ]
    return [
        {
            "name": "run_sequence",
            "description": "Run one of the player's named sequences.",
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sequences or [""]},
                    "say": {"type": "string", "description": "A short spoken line."},
                },
                "required": ["name", "say"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_actions",
            "description": "Press game actions, in order, with the player's own keys.",
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Action names from the list, e.g. v_flightready.",
                    },
                    "say": {"type": "string", "description": "A short spoken line."},
                },
                "required": ["actions", "say"],
                "additionalProperties": False,
            },
        },
        *looks,
    ]


def history(
    recent: Sequence[Exchange], text: str, today: datetime.date | None = None, about: str = ""
) -> list[MessageParam]:
    """Earlier exchanges as turns, then this request, headed by today's date and what
    Tycho knows about the player (``about``: their ships). Both live here, not in the
    briefing, so the cached briefing never changes."""
    messages: list[MessageParam] = []
    for exchange in recent:
        messages.append({"role": "user", "content": exchange.said})
        messages.append({"role": "assistant", "content": f"[Tycho] {exchange.outcome}"})
    if today is None:
        messages.append({"role": "user", "content": text})
    else:
        header = f"[{today.day} {today:%B %Y}]" + (f"\n{about}" if about else "")
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": header}, {"type": "text", "text": text}],
            }
        )
    return messages


class ClaudeBrain:
    def __init__(
        self,
        matcher: CommandMatcher,
        *,
        api_key: str | None = None,
        client: anthropic.AsyncAnthropic | None = None,
        model: str = MODEL,
        today: Callable[[], datetime.date] = datetime.date.today,
        lookups: Sequence[Lookup] = (),
        reference: str = "",
        about: Callable[[], str] = lambda: "",
        cards: Callable[[], bool] = lambda: False,
    ) -> None:
        """``reference`` is extra text for the end of the cached briefing, such as
        the ship table being measured against the ship look-up (ADR 15). ``about``
        says what Tycho knows about the player, for each request's header. ``cards``
        says whether the Tile is on, so whether Claude may show a card (#2)."""
        # The session gives up after its own deadline; one quick retry fits inside it.
        self._owns_client = client is None
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=anthropic.Timeout(8.0, connect=3.0), max_retries=1
        )
        self._matcher = matcher
        self._model = model
        self._today = today
        self._sequences = sorted(c.id for c in matcher.commands if c.is_sequence)
        self._lookups = {lookup.name: lookup for lookup in lookups}
        if clash := ACTS & self._lookups.keys():
            raise ValueError(f"a look-up can't be named {', '.join(sorted(clash))}")
        self._tools = _tools(self._sequences, lookups)
        self._reference = reference
        self._about = about
        self._cards = cards
        self._for: Keybinds | None = None
        self._system: list[TextBlockParam] = []
        self._actions: frozenset[str] = frozenset()
        self._preparing: asyncio.Future[list[None]] | None = None

    async def warm(self, keybinds: Keybinds) -> None:
        """Warm the cache, and load what the look-ups need, side by side.

        Called again after the keybinds change (#60): the briefing lists them, so the
        cache needs writing again. The look-ups load once, in a task of their own, so a
        rewarm that cancels this one can't leave them half loaded."""
        if self._preparing is None:
            lookups = self._lookups.values()
            self._preparing = asyncio.gather(*(lookup.prepare() for lookup in lookups))
        await asyncio.gather(self._warm_cache(keybinds), asyncio.shield(self._preparing))

    async def close(self) -> None:
        """Stop any loading still running, and close the connection to Claude, if it's
        the brain's own."""
        if self._preparing is not None and not self._preparing.done():
            self._preparing.cancel()
            await asyncio.gather(self._preparing, return_exceptions=True)
        if self._owns_client:
            await self._client.close()

    async def _warm_cache(self, keybinds: Keybinds) -> None:
        """Write the briefing to the cache and open the connection, billing no output.

        Everything up to the briefing must match ``decide``'s request, or the cache misses.
        """
        self._brief(keybinds)
        started = time.perf_counter()
        try:
            message = await self._client.messages.create(
                model=self._model,
                system=self._system,
                tools=self._offered(),
                tool_choice=TOOL_CHOICE,
                output_config={"effort": EFFORT},
                max_tokens=0,
                messages=[{"role": "user", "content": "ready"}],
            )
        except anthropic.APIError as e:
            log.warning("Couldn't warm up the brain: %s", e)
            return
        _log_usage(message.usage, started, "Brain warmed up")

    async def decide(self, request: BrainRequest) -> Decision:
        self._brief(request.keybinds)
        started = time.perf_counter()
        # The fleet line matches ship names, which is too slow for the event loop.
        about = await asyncio.to_thread(self._about)
        messages = history(request.recent, request.text, self._today(), about)
        turn = _Turn(waiting=request.waiting, looking=request.looking)
        try:
            for call in range(MAX_CALLS):
                decided, message, looking = await self._call(messages, started, turn)
                if decided is not None:
                    if turn.searched and not isinstance(decided, Reply):
                        log.warning("The brain chose to act after a web search; not pressing")
                        return Reply(NO_PRESS_AFTER_SEARCH)
                    if isinstance(decided, Reply):
                        return _credited(decided, turn.credits)
                    return decided
                _log_usage(message.usage, started)
                if message.stop_reason == "refusal":
                    raise BrainError("Claude declined")
                if not looking:
                    reply = spoken(" ".join(b.text for b in message.content if b.type == "text"))
                    if not reply:
                        raise BrainError(f"no answer (stopped: {message.stop_reason})")
                    if found := _TOOL_AS_TEXT.search(reply):
                        # 'show_card({"say": ...' read out is gibberish to the player:
                        # what came before it can be said; with nothing before, it fails.
                        reply = reply[: found.start()].strip()
                        if not reply:
                            raise BrainError("it wrote a tool call as text")
                    return _credited(Reply(reply[:400]), turn.credits)
                results = await asyncio.gather(*(task for _, task in looking))
                for (use_id, _), result in zip(looking, results, strict=True):
                    # Credited only for what it found: not for a miss, an error, or a
                    # look-up a card cut short (review of #179).
                    credit = turn.sources.get(use_id, "")
                    if credit and _found(result) and credit not in turn.credits:
                        turn.credits.append(credit)
                messages.append({"role": "assistant", "content": message.content})
                content: list[Any] = [*_results(looking, results)]
                if call == MAX_CALLS - 2:
                    # The next call is the last: answer with what's here, a card included;
                    # beside a refused act, pick again or answer, but look nothing up.
                    note = LAST_PICK if turn.refused else LAST_CALL
                    content.append({"type": "text", "text": note})
                messages.append({"role": "user", "content": content})
        except anthropic.AuthenticationError as e:
            raise BrainError("the Anthropic key was refused") from e
        except anthropic.APIConnectionError as e:
            raise BrainError("can't reach Claude (offline?)") from e
        except anthropic.APIStatusError as e:
            raise BrainError(f"Claude returned an error ({e.status_code})") from e
        if turn.unbound is not None and turn.refused:
            raise turn.unbound  # the last call too named actions that aren't bound
        raise BrainError(f"still looking things up after {MAX_CALLS} tries")

    async def _call(
        self, messages: list[MessageParam], started: float, turn: _Turn
    ) -> tuple[Decision | None, Any, list[tuple[str, asyncio.Task[LookupResult]]]]:
        """One call to Claude: an act if it picked one (the stream is left there),
        otherwise the whole message and the look-ups it started. Notes in ``turn``
        whether a look-up from the web started."""
        looking: list[tuple[str, asyncio.Task[LookupResult]]] = []
        checking = 0
        turn.refused = False
        instant: list[asyncio.Task[LookupResult]] = []
        """A fleet or settings note in the same reply as an act: it runs too (#156)."""
        try:
            async with self._client.messages.stream(
                model=self._model,
                system=self._system,
                tools=self._offered(),
                tool_choice=TOOL_CHOICE,
                output_config={"effort": EFFORT},
                max_tokens=MAX_TOKENS,
                messages=messages,
            ) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_stop"
                        and event.content_block.type == "tool_use"
                    ):
                        block = event.content_block
                        log.debug("Brain chose %s in %.0f ms", block.name, _ms(started))
                        if block.name in self._lookups:
                            # Start now; Claude's message may still be finishing.
                            chosen = self._lookups[block.name]
                            if getattr(chosen, "from_the_web", False):
                                log.debug("Brain searching the web at %.0f ms", _ms(started))
                                turn.searched = True
                                if turn.waiting is not None:
                                    turn.waiting()
                                    turn.waiting = None  # once a request
                                turn.looking = None  # not after a search's line
                            elif turn.looking is not None and not getattr(chosen, "instant", False):
                                # Told once the reply is known not to end in an act: the
                                # session moves its deadline and says "Checking." (#176).
                                checking += 1
                            seconds = float(getattr(chosen, "seconds", LOOKUP_SECONDS))
                            lookup = run_lookup(chosen, block.input, seconds)
                            turn.sources[block.id] = str(getattr(chosen, "credit", ""))
                            task = asyncio.create_task(lookup)
                            looking.append((block.id, task))
                            if getattr(chosen, "instant", False):
                                instant.append(task)
                            continue
                        if turn.refused:
                            # Only the first act in a reply may be pressed, even when that
                            # one was refused (#176): this one waits for the next call.
                            refused = SECOND_ACT.format(tool=block.name)
                            looking.append((block.id, _answered(failure(refused))))
                            continue
                        try:
                            decided = parse_tool_call(
                                block.name, block.input, self._actions, self._sequences
                            )
                        except UnboundAction as e:
                            # Not pressed: Claude hears why, with the closest bound names,
                            # and picks again on the next call (#164).
                            log.debug("Brain named unbound actions: %s", e.unknown)
                            turn.unbound, turn.refused = e, True
                            looking.append((block.id, _answered(_unbound(e, self._actions))))
                            continue
                        # Leaving the stream here ends the request, so only the first act
                        # in a reply is ever pressed. A look-up started with it is no longer
                        # needed; a fleet or settings note is ("I'm in my Pitbull, power
                        # off", #156): it's local and quick, so it finishes first.
                        for _, task in looking:
                            if task not in instant:
                                task.cancel()
                        if instant:
                            _, pending = await asyncio.wait(instant, timeout=INSTANT_SECONDS)
                            if pending:
                                log.warning(
                                    "Pressing before %d fleet or settings note(s) finished;"
                                    " they carry on",
                                    len(pending),
                                )
                        return decided, None, []
                message = await stream.get_final_message()
            for _ in range(checking if turn.looking is not None else 0):
                turn.looking()  # type: ignore[misc]
        except BaseException:
            for _, task in looking:
                task.cancel()
            raise
        return None, message, looking

    def _offered(self) -> list[ToolParam]:
        """The tools for this call: ``show_card`` only while the Tile is on. Turning
        the Tile on or off changes the cached prefix, so it costs one cache write."""
        if not self._cards():
            return self._tools
        return [*self._tools[:2], SHOW_CARD, *self._tools[2:]]

    def _brief(self, keybinds: Keybinds) -> None:
        """Rebuild the briefing only when the keybinds change, so the cache holds."""
        if keybinds is self._for:
            return
        self._for = keybinds
        self._actions = frozenset(offered_actions(keybinds, self._matcher))
        text = briefing(keybinds, self._matcher)
        if self._reference:
            text += f"\n{self._reference.strip()}\n"
        self._system = [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        ]


def _results(
    looking: list[tuple[str, asyncio.Task[LookupResult]]], results: Sequence[LookupResult]
) -> list[ToolResultBlockParam]:
    """Every look-up's result, in one turn, in the order Claude asked (ADR 15)."""
    return [
        {"type": "tool_result", "tool_use_id": use_id, "content": r.content, "is_error": r.is_error}
        for (use_id, _), r in zip(looking, results, strict=True)
    ]


_MARKUP = re.compile(r"[*`#]+")


def _answered(result: LookupResult) -> asyncio.Task[LookupResult]:
    """A result that's ready now, in the shape of a look-up still running."""

    async def ready() -> LookupResult:
        return result

    return asyncio.create_task(ready())


def _unbound(error: UnboundAction, actions: Collection[str]) -> LookupResult:
    """What Claude hears when it names actions that aren't bound: the closest that
    are, or that there's no key for it, so it says so."""
    close = {
        name: difflib.get_close_matches(name, actions, n=3, cutoff=0.5) for name in error.unknown
    }
    hints = "; ".join(
        f"{name}: did you mean {', '.join(found)}?" if found else f"{name}: nothing close is bound"
        for name, found in close.items()
    )
    return failure(
        f"Nothing was pressed. Not bound: {hints}. Pick from the bound actions, or say "
        "there's no key for it and what to bind."
    )


TOOL_CHOICE: Any = {"type": "auto"}
"""Several tools in one reply (#149, #156): look-ups at once instead of one call
after another, and a fleet or settings note alongside an act. Only the first act in
a reply is pressed (the stream is left there), and every act is still checked."""
INSTANT_SECONDS = 1.0
"""How long an act waits for a fleet or settings note from the same reply."""

SECOND_ACT = (
    "Not done: an earlier action in this reply was refused, so {tool} wasn't used. Call "
    "it again on its own if it's still wanted."
)


def _found(result: LookupResult) -> bool:
    """Whether a look-up's result holds data: not an error, not a miss."""
    return not result.is_error and '"not_found"' not in result.content


def _credited(reply: Reply, credits: list[str]) -> Reply:
    """The answer, naming each source it used that it didn't name itself: Claude is
    asked to credit UEX, but with the details on a card it often doesn't (#164)."""
    said = re.sub(r"\b(\w)\.(?=\w\.)", r"\1", reply.text)  # "U.E.X." names UEX too
    missing = [c for c in credits if not re.search(rf"\b{re.escape(c)}", said, re.IGNORECASE)]
    if not missing:
        return reply
    return dataclasses.replace(reply, text=f"{reply.text} From {' and '.join(missing)}.")


LAST_PICK = "No more look-ups for this request: pick the actions again, or say what you can't do."
"""Instead of LAST_CALL, when the turn holds a refused act (review of #177)."""

LAST_CALL = (
    "No more look-ups for this request. Answer now with what you found: speak, or "
    "use show_card. If something wasn't found, say so briefly."
)
"""Added to the results before the last call, so a third look-up isn't the end."""
_TOOL_AS_TEXT = re.compile(r"\b[a-z_]{3,}\s*\(\s*\{", re.IGNORECASE)
"""A tool call written as text: never said out loud."""


def spoken(text: str) -> str:
    """A reply as one line to say: no markdown (it would be read out), no line breaks."""
    return " ".join(_MARKUP.sub("", text).split())


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _log_usage(usage: Any, started: float, what: str = "Brain replied") -> None:
    tokens = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens or 0,
        usage.cache_creation_input_tokens or 0,
    )
    cost = sum(n * price for n, price in zip(tokens, _PRICES, strict=True)) / 1_000_000
    log.debug(
        "%s in %.0f ms: %d in, %d out, %d cached, %d cache write, about $%.5f",
        what,
        _ms(started),
        *tokens,
        cost,
    )
