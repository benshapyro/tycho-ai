"""The brain on any model OpenRouter offers (#192). Satisfies ``runtime.Brain``.

Players pick it with ``brain_provider = "openrouter"`` (#112), on a model from the
allowlist in ``providers``. The brain eval uses it (``tools/bench/tycho_eval.py
--provider openrouter --model <id>``) to measure any model on Tycho's real path; one
joins the allowlist only after it passes with zero wrong presses.

It works as ``ClaudeBrain`` does, through the same ``rounds``: the same briefing and
tools, an act taken as soon as its tool call is complete (the stream is left there),
look-ups in a loop of up to ``MAX_CALLS`` calls, and every pick checked by
``parse_tool_call``, then by the safety gate. OpenRouter speaks OpenAI's chat format,
so a tool call arrives as pieces of JSON: it's complete when its arguments parse as
a whole object, when the next tool call starts, or when the stream ends.

It has no web search of its own: that is Anthropic's server tool, run with the player's
Anthropic key if they have one (``knowledge.web``); without one, the brain says so.

By default OpenRouter picks the host that serves a model (mostly on price). The eval can
pin it (#267): ``hosts`` are tried in order with no fallback, and ``sort`` asks for the
fastest one. Each call logs the host that answered (``[host: Cerebras]``), so a latency
is traceable to who served it.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from tycho.brain.briefing import briefing, offered_actions
from tycho.brain.claude import turns
from tycho.brain.decisions import BrainError, BrainRequest, Decision, Reply
from tycho.brain.looking import openai_turns
from tycho.brain.lookups import Lookup
from tycho.brain.openrouter_http import ChatClient, Chunk, OpenRouterClient
from tycho.brain.player_keys import keyed
from tycho.brain.priming import Primer
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
from tycho.keybinds import Keybinds
from tycho.packs import GamePack
from tycho.tile.cards import show_card

log = logging.getLogger(__name__)

WARM_TOKENS = 16
"""The warm-up's reply: some providers refuse fewer."""
MAX_TOKENS = 2048
"""A how-to or a plan on a card ran past 1024 with Haiku (#266)."""
REASONING_TOKENS = 2048
"""Room for a model that reasons before it answers (#268): its reasoning counts against
``max_tokens``, and GLM, Qwen and Kimi ran out at 1024 with no answer. It's a cap, so a
model that doesn't reason pays nothing for it."""
SORTS = ("latency", "throughput", "price")
"""What OpenRouter can pick a host by (its ``provider.sort``)."""


@dataclass(frozen=True, slots=True)
class Prices:
    """Dollars per token, from OpenRouter's price list, for when a reply doesn't
    report its own cost."""

    prompt: float
    completion: float
    cache_read: float | None = None


@dataclass
class _Partial:
    """A tool call as its pieces stream in."""

    id: str = ""
    name: str = ""
    arguments: str = ""
    done: bool = False

    def parsed(self) -> object:
        """The arguments as an object, or the raw text if they aren't one."""
        try:
            return json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return self.arguments

    def echoed(self) -> str:
        """The arguments to send back with the call. Malformed ones (``{"name": ``) go
        back as ``{}``: a provider refuses a turn that holds invalid JSON, which would fail
        the whole request where the look-up has already answered with an error (#260)."""
        return self.arguments if isinstance(self.parsed(), dict) else "{}"

    def whole(self) -> bool:
        """Whether the arguments are already a complete object: nothing more can follow."""
        if not self.name or not self.arguments.rstrip().endswith("}"):
            return False
        return isinstance(self.parsed(), dict)


@dataclass
class _Message:
    """One reply as it streams in: its text, its tool calls, how it finished."""

    text: str = ""
    calls: list[_Partial] = field(default_factory=list[_Partial])
    """The complete tool calls, in order."""
    finish: str = ""
    usage: Mapping[str, Any] | None = None
    host: str = ""
    """Who served the call (OpenRouter's ``provider``: "Cerebras", "Groq")."""
    generation: str = ""
    """OpenRouter's id for the call, to look its cost up by."""
    _partials: dict[int, _Partial] = field(default_factory=dict[int, _Partial])

    def read(self, chunk: Chunk) -> list[_Partial]:
        """Take in one chunk. Returns the tool calls it completed, in order."""
        if (error := chunk.get("error")) is not None:
            raise BrainError(f"OpenRouter: {_error_text(error)}")
        self.generation = self.generation or str(chunk.get("id") or "")
        self.host = self.host or str(chunk.get("provider") or "")
        self.usage = chunk.get("usage") or self.usage
        complete: list[_Partial] = []
        choices: Sequence[Mapping[str, Any]] = chunk.get("choices") or ()
        for choice in choices:
            delta: Mapping[str, Any] = choice.get("delta") or {}
            if content := delta.get("content"):
                self.text += str(content)
            pieces: Sequence[Mapping[str, Any]] = delta.get("tool_calls") or ()
            for piece in pieces:
                index = int(piece.get("index", len(self._partials)))
                # A new tool call means every one before it is complete.
                complete += self._complete(i for i in sorted(self._partials) if i < index)
                partial = self._partials.setdefault(index, _Partial())
                partial.id = partial.id or str(piece.get("id") or f"call_{index}")
                function: Mapping[str, Any] = piece.get("function") or {}
                partial.name = partial.name or str(function.get("name") or "")
                partial.arguments += str(function.get("arguments") or "")
                if partial.whole():
                    complete += self._complete([index])
            if finish := choice.get("finish_reason"):
                self.finish = str(finish)
        return complete

    def rest(self) -> list[_Partial]:
        """The stream ended: every tool call still open is as complete as it'll get."""
        return self._complete(sorted(self._partials))

    def _complete(self, indexes: Iterable[int]) -> list[_Partial]:
        done: list[_Partial] = []
        for index in indexes:
            partial = self._partials[index]
            if not partial.done:
                partial.done = True
                self.calls.append(partial)
                done.append(partial)
        return done


class OpenRouterBrain:
    def __init__(
        self,
        matcher: CommandMatcher,
        *,
        model: str,
        api_key: str | None = None,
        client: ChatClient | None = None,
        today: Callable[[], datetime.date] = datetime.date.today,
        lookups: Sequence[Lookup] = (),
        reference: str = "",
        about: Callable[[], str] = lambda: "",
        cards: Callable[[], bool] = lambda: False,
        effort: str | None = None,
        prices: Prices | None = None,
        hosts: Sequence[str] = (),
        sort: str | None = None,
        traces: TraceFile | None = None,
        pack: GamePack,
    ) -> None:
        """``model`` is OpenRouter's id ("anthropic/claude-haiku-5.5"). ``effort`` asks a
        model that reasons to reason that hard ("low"); by default nothing is asked, so
        each model runs as its provider sets it. ``prices`` is for replies that don't
        report their cost. ``hosts`` pins who serves the model, by OpenRouter's slugs
        ("cerebras", "groq"), tried in order and never falling back to another; ``sort``
        picks the host by "latency", "throughput" or "price" (#267). The rest is as
        ``ClaudeBrain``, ``traces`` too (#282)."""
        if client is None and not api_key:
            raise ValueError("an OpenRouter key or a client is needed")
        self._owns_client = client is None
        self._client: ChatClient = client or OpenRouterClient(api_key or "")
        self._matcher = matcher
        self._pack = pack
        """The game the briefing is for (ADR 18)."""
        self._model = model
        self._today = today
        self._effort = effort
        self._prices = prices
        if sort is not None and sort not in SORTS:
            raise ValueError(f"OpenRouter can't sort hosts by {sort!r}")
        self._route = route(hosts, sort)
        self._keys = pack.mode == "keys"
        """Whether the brain may press: in advisor mode it has no tool to (#191)."""
        self._sequences = (
            sorted(c.id for c in matcher.commands if c.is_sequence) if self._keys else []
        )
        lookups, self._lookups = keyed(lookups, self._keys, lambda: self._for)
        if clash := ACTS & self._lookups.keys():
            raise ValueError(f"a look-up can't be named {', '.join(sorted(clash))}")
        specs = tool_specs(self._sequences, lookups, keys=self._keys)
        self._tools = [_function(spec) for spec in specs]
        self._reference = reference
        self._about = about
        self._cards = cards
        self._traces = traces
        self._briefed = ""
        self._for: Keybinds | None = None
        self._system: dict[str, object] = {}
        self._actions: frozenset[str] = frozenset()
        self._preparing: asyncio.Future[list[None]] | None = None
        self._primer = Primer(self._client.connect, self._rewarm)

    def prime(self) -> None:
        """As ``ClaudeBrain.prime``: get ready while the player talks (#21)."""
        self._primer.prime()

    async def _rewarm(self) -> bool:
        return self._for is not None and await self._warm_cache(self._for)

    async def warm(self, keybinds: Keybinds) -> None:
        """Warm the cache (for models that cache), and load what the look-ups need."""
        if self._preparing is None:
            lookups = self._lookups.values()
            self._preparing = asyncio.gather(*(lookup.prepare() for lookup in lookups))
        warming = self._primer.warming(lambda: self._warm_cache(keybinds))
        await asyncio.gather(warming, asyncio.shield(self._preparing))

    async def close(self) -> None:
        if self._preparing is not None and not self._preparing.done():
            self._preparing.cancel()
            await asyncio.gather(self._preparing, return_exceptions=True)
        await self._primer.close()
        for lookup in self._lookups.values():
            if (close := getattr(lookup, "close", None)) is not None:
                with contextlib.suppress(Exception):
                    await close()
        if self._owns_client:
            await self._client.close()

    async def _warm_cache(self, keybinds: Keybinds) -> bool:
        """One short request with the same briefing and tools as ``decide``'s."""
        self._brief(keybinds)
        started = time.perf_counter()
        body = self._body([{"role": "user", "content": "ready"}], max_tokens=WARM_TOKENS)
        try:
            async with self._client.stream(body) as stream:
                usage, host = None, ""
                async for chunk in stream:
                    usage = chunk.get("usage") or usage
                    host = host or str(chunk.get("provider") or "")
        except BrainError as e:
            log.warning("Couldn't warm up the brain: %s", e)
            return False
        self._primer.used()
        self._log_usage(usage, started, "Brain warmed up", host)
        return True

    async def decide(self, request: BrainRequest) -> Decision:
        self._brief(request.keybinds)
        turn = Turn.of(request, self._primer.used)
        if self._traces is not None:
            turn.trace = self._traces.start(
                "openrouter", self._model, self._briefed, request.text, len(request.recent)
            )
        await self._primer.going()
        decided = await traced(self._traces, turn, self._decide(request, turn))
        self._primer.used()  # and it read the cache
        return decided

    async def _decide(self, request: BrainRequest, turn: Turn) -> Decision:
        started = time.perf_counter()
        about = await asyncio.to_thread(self._about)
        messages = openai_turns(turns(request, self._today(), about))
        for call in range(MAX_CALLS):
            decided, message, round_ = await self._call(messages, started, turn)
            if decided is not None:
                if turn.searched and not isinstance(decided, Reply):
                    log.warning("The brain chose to act after a web search; not pressing")
                    return Reply(NO_PRESS_AFTER_SEARCH)
                if isinstance(decided, Reply):
                    return credited(decided, turn.credits)
                return decided
            tokens, cost, priced = self._log_usage(message.usage, started, host=message.host)
            round_.used(tokens, cost, message.finish or "unknown", message.host, priced)
            if message.finish == "content_filter":
                raise BrainError("the model declined")
            if not round_.looking:
                return final_reply(message.text, turn.credits, message.finish or "unknown")
            results = await round_.results()
            messages.append(
                {
                    "role": "assistant",
                    "content": message.text or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.echoed()},
                        }
                        for c in message.calls
                    ],
                }
            )
            for (use_id, _), result in zip(round_.looking, results, strict=True):
                messages.append({"role": "tool", "tool_call_id": use_id, "content": result.content})
            if note := last_note(call, turn, self._cards()):
                messages.append({"role": "user", "content": note})
        if turn.unbound is not None and turn.refused:
            raise turn.unbound  # the last call too named actions that aren't bound
        raise BrainError(f"still looking things up after {MAX_CALLS} tries")

    async def _call(
        self, messages: list[dict[str, Any]], started: float, turn: Turn
    ) -> tuple[Decision | None, _Message, Round]:
        """One call: an act if the model picked one (the stream is left there),
        otherwise the whole reply and the round, with the look-ups it started."""
        round_ = Round(turn, self._lookups, self._actions, self._sequences, started)
        message = _Message()
        try:
            async with self._client.stream(self._body(messages)) as stream:
                async for chunk in stream:
                    round_.streaming(answering="choices" in chunk or "usage" in chunk)
                    for call in message.read(chunk):
                        if (
                            decided := await self._tool_call(round_, call, message, started)
                        ) is not None:
                            return decided, message, round_
                for call in message.rest():
                    if message.finish == "length" and not call.whole():
                        # Cut off at the output limit (#266): its arguments end mid-way.
                        message.calls.remove(call)
                        tokens, cost, priced = self._log_usage(
                            message.usage, started, host=message.host
                        )
                        round_.used(tokens, cost, "length", message.host, priced)
                        return await round_.cut_short(call.name), message, round_
                    if (
                        decided := await self._tool_call(round_, call, message, started)
                    ) is not None:
                        return decided, message, round_
            round_.finished()
        except BaseException:
            round_.cancel()
            raise
        return None, message, round_

    async def _tool_call(
        self, round_: Round, call: _Partial, message: _Message, started: float
    ) -> Decision | None:
        decided = await round_.tool_call(call.id, call.name, call.parsed())
        if decided is not None:
            round_.left(len(message.text) + sum(len(c.arguments) for c in message.calls))
            # The call ends here, so its usage never arrives: the eval looks its cost
            # up by the generation's id.
            log.debug(
                "Brain acted in %.0f ms, generation %s%s",
                ms(started),
                message.generation or "?",
                _served(message.host),
            )
            if round_.turn.trace is not None:
                round_.turn.trace.served(message.host, message.generation or "")
        return decided

    def _body(
        self, messages: list[dict[str, Any]], max_tokens: int = MAX_TOKENS + REASONING_TOKENS
    ) -> dict[str, Any]:
        tools = self._tools
        if self._cards():
            acts = 2 if self._keys else 0  # after the acts, if offered
            tools = [*tools[:acts], _function(show_card(self._pack.tile.cards)), *tools[acts:]]
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [self._system, *messages],
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "max_tokens": max_tokens,
            "stream": True,
            "usage": {"include": True},
        }
        body["provider"] = self._route
        if self._effort is not None:
            body["reasoning"] = {"effort": self._effort, "exclude": True}
        return body

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
        # Marked for the models that need it (OpenRouter drops it for others); Claude's: 1 h.
        cache = {"type": "ephemeral"} | ({"ttl": "1h"} if "anthropic/" in self._model else {})
        part = {"type": "text", "text": text, "cache_control": cache}
        self._system = {"role": "system", "content": [part]}
        self._briefed = briefing_hash(text)

    def _log_usage(
        self,
        usage: Mapping[str, Any] | None,
        started: float,
        what: str = "Brain replied",
        host: str = "",
    ) -> tuple[dict[str, int], float | None, str]:
        """Log a call's tokens and cost; returns them, and where the cost came from, for
        the trace."""
        if not usage:
            log.debug("%s in %.0f ms: no usage reported%s", what, ms(started), _served(host))
            return {}, None, ""
        prompt, completion = (
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
        details: Mapping[str, Any] = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        cost, source = usage.get("cost"), "reported"
        if not isinstance(cost, int | float) and self._prices is not None:
            p = self._prices
            read = p.cache_read if p.cache_read is not None else p.prompt
            cost = (prompt - cached) * p.prompt + cached * read + completion * p.completion
            source = "price list"
        priced = f", about ${cost:.5f} ({source})" if isinstance(cost, int | float) else ""
        log.debug(
            "%s in %.0f ms: %d in, %d out, %d cached%s%s",
            what,
            ms(started),
            prompt,
            completion,
            cached,
            priced,
            _served(host),
        )
        tokens = {"input": prompt, "output": completion, "cache_read": cached}
        if not isinstance(cost, int | float):
            return tokens, None, ""
        return tokens, float(cost), source


def route(hosts: Sequence[str] = (), sort: str | None = None) -> dict[str, object]:
    """OpenRouter's ``provider`` field: never a host that keeps data or trains on it (#488),
    then pinned ``hosts`` and a ``sort``. Pinned hosts never fall back to another: a run
    measures the host it names, or fails."""
    pinned: dict[str, object] = {"data_collection": "deny"}
    if names := [h.strip().lower() for h in hosts if h.strip()]:
        pinned |= {"order": names, "allow_fallbacks": False}
    if sort is not None:
        pinned["sort"] = sort
    return pinned


def _served(host: str) -> str:
    """The end of a call's log line: who served it, for the eval to read (#267)."""
    return f" [host: {host}]" if host else ""


def _function(spec: Mapping[str, object]) -> dict[str, object]:
    """A tool in OpenAI's shape. Not ``strict``: few models other than OpenAI's accept
    it, and every pick is checked by ``parse_tool_call`` anyway."""
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["input_schema"],
        },
    }


def _error_text(error: object) -> str:
    if isinstance(error, dict):
        message: object = cast(dict[str, Any], error).get("message")
        if message:
            return str(message)
    return str(cast(object, error))[:200]
