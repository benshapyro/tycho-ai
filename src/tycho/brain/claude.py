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
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping, Sequence
from typing import Any, cast

import anthropic
from anthropic.types import (
    ImageBlockParam,
    MessageParam,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
)

from tycho.brain.briefing import briefing, offered_actions
from tycho.brain.decisions import BrainError, BrainRequest, Decision, Exchange, Reply
from tycho.brain.looking import MAY_OFFER, SEEN, picture_block
from tycho.brain.lookups import Lookup, LookupResult
from tycho.brain.player_keys import keyed
from tycho.brain.priming import Primer
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
from tycho.eyes import Picture
from tycho.keybinds import Keybinds
from tycho.packs import GamePack
from tycho.tile.cards import show_card

log = logging.getLogger(__name__)

MODEL = ANTHROPIC_HAIKU
EFFORT = "low"
"""97% on the 82-case eval at about 1.2 s; medium scored the same, slower (ADR 6)."""
MAX_TOKENS = 2048
"""Replies are a sentence or two and tool calls a few dozen tokens, but a how-to or a
plan on a card ran past 1024 (#266). It's a cap: a short reply pays nothing for it."""
DRAIN_SECONDS = 10.0
"""The most a stream an act left is read for, in the background, before it's closed
(#518): longer than the client's 8 s read timeout."""
# Haiku 5.5 per million tokens: input, output, cache read, cache write (1 hour).
_PRICES = (0.10, 0.50, 0.01, 0.20)


def _tools(
    sequences: list[str], lookups: Iterable[Lookup] = (), *, keys: bool = True
) -> list[ToolParam]:
    specs = tool_specs(sequences, lookups, keys=keys)
    return [cast(ToolParam, {**spec, "strict": True}) for spec in specs]


def history(
    recent: Sequence[Exchange],
    text: str,
    today: datetime.date | None = None,
    about: str = "",
    own_keys: str = "",
    *,
    picture: Picture | None = None,
    may_look: bool = False,
) -> list[MessageParam]:
    """Earlier exchanges as turns, then this request, headed by today's date, what
    Tycho knows about the player (``about``: their ships) and, with key actions off,
    their own keys for what they asked (#386). With a look (#31), the picture of the
    game window goes before the words; with looking on and no picture, the brain is
    told it may offer one. All live here, not in the briefing, so the cached briefing
    never changes, and the picture is in this request's turn only."""
    messages: list[MessageParam] = []
    for exchange in recent:
        messages.append({"role": "user", "content": exchange.said})
        messages.append({"role": "assistant", "content": f"[Tycho] {exchange.outcome}"})
    if today is None:
        messages.append({"role": "user", "content": text})
    else:
        header = f"[{today.day} {today:%B %Y}]" + (f"\n{about}" if about else "")
        if own_keys:
            header += f"\n[The player's own keys for this: {own_keys}]"
        if picture is not None:
            header += f"\n{SEEN}"
        elif may_look:
            header += f"\n{MAY_OFFER}"
        content: list[TextBlockParam | ImageBlockParam] = [{"type": "text", "text": header}]
        if picture is not None:
            content.append(picture_block(picture))
        content.append({"type": "text", "text": text})
        messages.append({"role": "user", "content": content})
    return messages


def turns(request: BrainRequest, today: datetime.date, about: str) -> list[MessageParam]:
    """``history`` for one request: its earlier exchanges, words, keys and picture."""
    r = request
    return history(
        r.recent, r.text, today, about, r.own_keys, picture=r.picture, may_look=r.may_look
    )


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
        pack: GamePack,
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
        """The game the briefing is for (ADR 18)."""
        self._model = model
        self._today = today
        self._keys = pack.mode == "keys"
        """Whether the brain may press: in advisor mode it has no tool to (#191)."""
        self._sequences = (
            sorted(c.id for c in matcher.commands if c.is_sequence) if self._keys else []
        )
        lookups, self._lookups = keyed(lookups, self._keys, lambda: self._for)
        if clash := ACTS & self._lookups.keys():
            raise ValueError(f"a look-up can't be named {', '.join(sorted(clash))}")
        self._tools = _tools(self._sequences, lookups, keys=self._keys)
        card = show_card(pack.tile.cards)  # its keys in the order the cache has (#576)
        name, described, schema = card["name"], card["description"], card["input_schema"]
        self._show_card = cast(
            ToolParam,
            {"name": name, "description": described, "strict": True, "input_schema": schema},
        )
        """Offered only while the Tile is on, so Claude never says "on the Tile" without
        one; only the kinds this game's Settings can hide (#528)."""
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
        self._draining: set[asyncio.Task[None]] = set()
        """Streams an act left, being read to the end (``_drain``)."""
        self._primer = Primer(self._connect, self._rewarm)

    def prime(self) -> None:
        """Push-to-talk went down: open the connection, or write the cache again
        after a long break, while the player talks (#21, ``priming``)."""
        self._primer.prime()

    async def _connect(self) -> None:
        """Open a connection with a free, tiny request: the model's details."""
        quick = self._client.with_options(max_retries=0, timeout=3.0)
        await quick.models.retrieve(self._model)

    async def _rewarm(self) -> bool:
        return self._for is not None and await self._warm_cache(self._for)

    async def warm(self, keybinds: Keybinds) -> None:
        """Warm the cache, and load what the look-ups need, side by side.

        Called again after the keybinds change (#60): the briefing lists them, so the
        cache needs writing again. The look-ups load once, in a task of their own, so a
        rewarm that cancels this one can't leave them half loaded."""
        if self._preparing is None:
            lookups = self._lookups.values()
            self._preparing = asyncio.gather(*(lookup.prepare() for lookup in lookups))
        warming = self._primer.warming(lambda: self._warm_cache(keybinds))
        await asyncio.gather(warming, asyncio.shield(self._preparing))

    async def close(self) -> None:
        """Stop any loading still running, and close the connection to Claude, if it's
        the brain's own."""
        if self._preparing is not None and not self._preparing.done():
            self._preparing.cancel()
            await asyncio.gather(self._preparing, return_exceptions=True)
        await self._primer.close()
        for task in self._draining:  # each closes its stream as it's cancelled
            task.cancel()
        await asyncio.gather(*self._draining, return_exceptions=True)
        for lookup in self._lookups.values():  # fetches that outlived their look-up (#240)
            if (close := getattr(lookup, "close", None)) is not None:
                with contextlib.suppress(Exception):
                    await close()
        if self._owns_client:
            await self._client.close()

    async def _warm_cache(self, keybinds: Keybinds) -> bool:
        """Write the briefing to the cache and open the connection, billing no output.
        Returns whether it did.

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
            return False
        self._primer.used()
        _log_usage(message.usage, started, "Brain warmed up")
        return True

    async def decide(self, request: BrainRequest) -> Decision:
        self._brief(request.keybinds)
        turn = Turn.of(request, self._primer.used)
        if self._traces is not None:
            turn.trace = self._traces.start(
                "anthropic", self._model, self._briefed, request.text, len(request.recent)
            )
        await self._primer.going()
        decided = await traced(self._traces, turn, self._decide(request, turn))
        self._primer.used()  # and it read the cache
        return decided

    async def _decide(self, request: BrainRequest, turn: Turn) -> Decision:
        started = time.perf_counter()
        # The fleet line matches ship names, which is too slow for the event loop.
        about = await asyncio.to_thread(self._about)
        messages = turns(request, self._today(), about)
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
                if note := last_note(call, turn, self._cards()):
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
        opened = contextlib.AsyncExitStack()
        try:
            async with opened:
                stream = await opened.enter_async_context(
                    self._client.messages.stream(
                        model=self._model,
                        system=self._system,
                        tools=tools,
                        tool_choice=TOOL_CHOICE,
                        output_config={"effort": EFFORT},
                        max_tokens=MAX_TOKENS,
                        messages=messages,
                    )
                )
                # Closed here, not by the garbage collector after an act leaves it:
                # that printed "async generator already executing" (#443).
                events = await opened.enter_async_context(contextlib.aclosing(_events(stream)))
                async for event in events:
                    round_.streaming()
                    if (
                        event.type == "content_block_stop"
                        and event.content_block.type == "tool_use"
                    ):
                        block = event.content_block
                        if _cut_off(block.name, block.input, tools):
                            round_.left(*_written(stream.current_message_snapshot))
                            self._drain(events, opened.pop_all())
                            return await round_.cut_short(block.name), None, round_
                        decided = await round_.tool_call(block.id, block.name, block.input)
                        if decided is not None:
                            round_.left(*_written(stream.current_message_snapshot))
                            self._drain(events, opened.pop_all())
                            return decided, None, round_
                message = await stream.get_final_message()
            round_.finished()
        except BaseException:
            round_.cancel()
            raise
        return None, message, round_

    def _drain(self, events: AsyncGenerator[Any], opened: contextlib.AsyncExitStack) -> None:
        """Read the rest of a stream an act left, then close it, in the background.

        Closing it at the act left the SDK's inner generators (the SSE decoder, httpx's
        byte streams) to the garbage collector, which closed them out of order and
        printed "async generator already executing" during later look-ups (#518). Read
        to the end, each finishes in turn. The act doesn't wait: it's pressed at once."""

        async def drain() -> None:
            try:
                async with asyncio.timeout(DRAIN_SECONDS):
                    async for _ in events:
                        pass
            except Exception as e:  # the answer is already decided
                log.debug("Reading the rest of a stream an act left: %s", e)
            finally:
                await opened.aclose()

        task = asyncio.get_running_loop().create_task(drain())
        self._draining.add(task)
        task.add_done_callback(self._draining.discard)

    def _offered(self) -> list[ToolParam]:
        """The tools for this call: ``show_card`` only while the Tile is on. Turning
        the Tile on or off changes the cached prefix, so it costs one cache write."""
        if not self._cards():
            return self._tools
        acts = 2 if self._keys else 0  # after run_sequence and run_actions, if offered
        return [*self._tools[:acts], self._show_card, *self._tools[acts:]]

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


def _written(message: Any) -> tuple[int, dict[str, int]]:
    """What a call left at an act had written, in characters, and its input and cache
    tokens: Claude sends those as the call starts, but the output count only at its end,
    which Tycho never reads (#387)."""
    chars = 0
    for block in message.content:
        if block.type == "text":
            chars += len(block.text)
        elif block.type == "tool_use":
            chars += len(json.dumps(block.input, ensure_ascii=False))
    usage = message.usage
    tokens = {
        "input": usage.input_tokens,
        "cache_read": usage.cache_read_input_tokens or 0,
        "cache_write": usage.cache_creation_input_tokens or 0,
    }
    return chars, tokens


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


def _events(stream: Any) -> AsyncGenerator[Any]:
    """The stream's events as the async generator the SDK makes, so it can be closed."""
    return cast(AsyncGenerator[Any], aiter(stream))
