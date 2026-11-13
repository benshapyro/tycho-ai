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
import contextlib
import datetime
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, cast

import anthropic
from anthropic.types import MessageParam, TextBlockParam, ToolParam, ToolResultBlockParam

from tycho.brain.briefing import briefing, offered_actions
from tycho.brain.decisions import BrainError, BrainRequest, Decision, Exchange, Reply
from tycho.brain.lookups import Lookup, LookupResult
from tycho.brain.providers import ANTHROPIC_HAIKU
from tycho.brain.rounds import (
    ACTS,
    MAX_CALLS,
    NO_PRESS_AFTER_SEARCH,
    Round,
    Turn,
    credited,
    final_reply,
    last_note,
    ms,
    tool_specs,
    traced,
)
from tycho.brain.traces import TraceFile, briefing_hash
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Keybinds
from tycho.packs import GamePack
from tycho.tile.cards import TOOL_DESCRIPTION, TOOL_SCHEMA

log = logging.getLogger(__name__)

MODEL = ANTHROPIC_HAIKU
EFFORT = "low"
"""97% on the 82-case eval at about 1.2 s; medium scored the same, slower (ADR 6)."""
MAX_TOKENS = 2048
"""Replies are a sentence or two and tool calls a few dozen tokens, but a how-to or a
plan on a card ran past 1024 (#266). It's a cap: a short reply pays nothing for it."""
# Haiku 5.5 per million tokens: input, output, cache read, cache write (1 hour).
_PRICES = (0.10, 0.50, 0.01, 0.20)


SHOW_CARD: ToolParam = {
    "name": "show_card",
    "description": TOOL_DESCRIPTION,
    "strict": True,
    "input_schema": dict(TOOL_SCHEMA),
}
"""Offered only while the Tile is on, so Claude never says "on the Tile" without one."""


def _tools(
    sequences: list[str], lookups: Iterable[Lookup] = (), *, keys: bool = True
) -> list[ToolParam]:
    specs = tool_specs(sequences, lookups, keys=keys)
    return [cast(ToolParam, {**spec, "strict": True}) for spec in specs]


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
        traces: TraceFile | None = None,
        pack: GamePack | None = None,
    ) -> None:
        """``reference`` is extra text for the end of the cached briefing, such as
        the ship table being measured against the ship look-up (ADR 15). ``about``
        says what Tycho knows about the player, for each request's header. ``cards``
        says whether the Tile is on, so whether Claude may show a card (#2).
        ``traces`` gets a line for each request when tracing is on (#282)."""
        # The session gives up after its own deadline; one quick retry fits inside it.
        self._owns_client = client is None
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=anthropic.Timeout(8.0, connect=3.0), max_retries=1
        )
        self._matcher = matcher
        self._pack = pack
        """The game the briefing is for (Star Citizen when None, ADR 18)."""
        self._model = model
        self._today = today
        self._keys = pack is None or pack.mode == "keys"
        """Whether the brain may press: in advisor mode it has no tool to (#191)."""
        self._sequences = (
            sorted(c.id for c in matcher.commands if c.is_sequence) if self._keys else []
        )
        self._lookups = {lookup.name: lookup for lookup in lookups}
        if clash := ACTS & self._lookups.keys():
            raise ValueError(f"a look-up can't be named {', '.join(sorted(clash))}")
        self._tools = _tools(self._sequences, lookups, keys=self._keys)
        self._reference = reference
        self._about = about
        self._cards = cards
        self._traces = traces
        self._briefed = ""
        """The briefing's hash, for the traces."""
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
        for lookup in self._lookups.values():  # fetches that outlived their look-up (#240)
            if (close := getattr(lookup, "close", None)) is not None:
                with contextlib.suppress(Exception):
                    await close()
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
        turn = Turn(waiting=request.waiting, looking=request.looking, checking=request.checking)
        if self._traces is not None:
            turn.trace = self._traces.start(
                "anthropic", self._model, self._briefed, request.text, len(request.recent)
            )
        return await traced(self._traces, turn, self._decide(request, turn))

    async def _decide(self, request: BrainRequest, turn: Turn) -> Decision:
        started = time.perf_counter()
        # The fleet line matches ship names, which is too slow for the event loop.
        about = await asyncio.to_thread(self._about)
        messages = history(request.recent, request.text, self._today(), about)
        try:
            for call in range(MAX_CALLS):
                decided, message, round_ = await self._call(messages, started, turn)
                if decided is not None:
                    if turn.searched and not isinstance(decided, Reply):
                        log.warning("The brain chose to act after a web search; not pressing")
                        return Reply(NO_PRESS_AFTER_SEARCH)
                    if isinstance(decided, Reply):
                        return credited(decided, turn.credits)
                    return decided
                tokens, cost = _log_usage(message.usage, started)
                round_.used(tokens, cost, str(message.stop_reason), priced="estimate")
                if message.stop_reason == "refusal":
                    raise BrainError("Claude declined")
                if not round_.looking:
                    text = " ".join(b.text for b in message.content if b.type == "text")
                    return final_reply(text, turn.credits, str(message.stop_reason))
                if message.stop_reason == "max_tokens" and _unanswered(message, round_):
                    # A call cut with no block end never got a result: sent back, Claude
                    # would refuse the request (400), so it ends here (#287).
                    return await round_.cut_short("tool call")
                results = await round_.results()
                messages.append({"role": "assistant", "content": message.content})
                content: list[Any] = [*_results(round_.looking, results)]
                if note := last_note(call, turn):
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
        self, messages: list[MessageParam], started: float, turn: Turn
    ) -> tuple[Decision | None, Any, Round]:
        """One call to Claude: an act if it picked one (the stream is left there),
        otherwise the whole message and the round, with the look-ups it started."""
        round_ = Round(turn, self._lookups, self._actions, self._sequences, started)
        tools = self._offered()
        try:
            async with self._client.messages.stream(
                model=self._model,
                system=self._system,
                tools=tools,
                tool_choice=TOOL_CHOICE,
                output_config={"effort": EFFORT},
                max_tokens=MAX_TOKENS,
                messages=messages,
            ) as stream:
                async for event in stream:
                    round_.streaming()
                    if (
                        event.type == "content_block_stop"
                        and event.content_block.type == "tool_use"
                    ):
                        block = event.content_block
                        if _cut_off(block.name, block.input, tools):
                            return await round_.cut_short(block.name), None, round_
                        decided = await round_.tool_call(block.id, block.name, block.input)
                        if decided is not None:
                            return decided, None, round_
                message = await stream.get_final_message()
            round_.finished()
        except BaseException:
            round_.cancel()
            raise
        return None, message, round_

    def _offered(self) -> list[ToolParam]:
        """The tools for this call: ``show_card`` only while the Tile is on. Turning
        the Tile on or off changes the cached prefix, so it costs one cache write."""
        if not self._cards():
            return self._tools
        acts = 2 if self._keys else 0  # after run_sequence and run_actions, if offered
        return [*self._tools[:acts], SHOW_CARD, *self._tools[acts:]]

    def _brief(self, keybinds: Keybinds) -> None:
        """Rebuild the briefing only when the keybinds change, so the cache holds."""
        if keybinds is self._for:
            return
        self._for = keybinds
        # In advisor mode nothing is offered, so a pick can only be refused (#191).
        offered = offered_actions(keybinds, self._matcher, self._pack) if self._keys else {}
        self._actions = frozenset(offered)
        text = briefing(keybinds, self._matcher, self._pack)
        if self._reference:
            text += f"\n{self._reference.strip()}\n"
        self._system = [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        ]
        self._briefed = briefing_hash(text)


def _results(
    looking: list[tuple[str, asyncio.Task[LookupResult]]], results: Sequence[LookupResult]
) -> list[ToolResultBlockParam]:
    """Every look-up's result, in one turn, in the order Claude asked (ADR 15)."""
    return [
        {"type": "tool_result", "tool_use_id": use_id, "content": r.content, "is_error": r.is_error}
        for (use_id, _), r in zip(looking, results, strict=True)
    ]


def _unanswered(message: Any, round_: Round) -> bool:
    """Whether the message holds a tool call no look-up or refusal answers: one the
    output limit cut before its block ended (#287)."""
    answered = {use_id for use_id, _ in round_.looking}
    return any(b.type == "tool_use" and b.id not in answered for b in message.content)


def _cut_off(name: str, arguments: object, tools: Sequence[ToolParam]) -> bool:
    """Whether an act was cut off at the output limit (#266). The SDK reads a cut
    call's arguments as far as they go, so a card arrives with its last rows and fields
    missing, and a cut run_actions would press only the actions it got to. Every tool
    is strict, so a whole call always has every required field. (A look-up's arguments
    are short, and checked by the look-up itself.)"""
    if name not in ACTS:
        return False
    if not isinstance(arguments, dict):
        return True
    for tool in tools:
        if tool["name"] == name:
            schema = cast(Mapping[str, Any], tool.get("input_schema", {}))
            required: Sequence[str] = schema.get("required", ())
            return any(key not in arguments for key in required)
    return False


TOOL_CHOICE: Any = {"type": "auto"}
"""Several tools in one reply (#149, #156): look-ups at once instead of one call
after another, and a fleet or settings note alongside an act. Only the first act in
a reply is pressed (the stream is left there), and every act is still checked."""


def _log_usage(
    usage: Any, started: float, what: str = "Brain replied"
) -> tuple[dict[str, int], float]:
    """Log a call's tokens and cost; returns them, for the trace."""
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
        ms(started),
        *tokens,
        cost,
    )
    names = ("input", "output", "cache_read", "cache_write")
    return dict(zip(names, tokens, strict=True)), round(cost, 7)
