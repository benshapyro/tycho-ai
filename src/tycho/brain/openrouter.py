"""The brain on any model OpenRouter offers (#192). Satisfies ``runtime.Brain``.

Not a player setting yet: the brain eval uses it (``tools/bench/tycho_eval.py
--provider openrouter --model <id>``) to measure other models on Tycho's real path,
and #112 adds the setting once a model passes with zero wrong presses.

It works as ``ClaudeBrain`` does, through the same ``rounds``: the same briefing and
tools, an act taken as soon as its tool call is complete (the stream is left there),
look-ups in a loop of up to ``MAX_CALLS`` calls, and every pick checked by
``parse_tool_call``, then by the safety gate. OpenRouter speaks OpenAI's chat format,
so a tool call arrives as pieces of JSON: it's complete when its arguments parse as
a whole object, when the next tool call starts, or when the stream ends.

It has no web search: that is Anthropic's own server tool (``knowledge.web``).
"""

import asyncio
import contextlib
import datetime
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import httpx2

from tycho.brain.briefing import briefing, offered_actions
from tycho.brain.claude import history
from tycho.brain.decisions import BrainError, BrainRequest, Decision, Exchange, Reply
from tycho.brain.lookups import Lookup
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
)
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Keybinds
from tycho.tile.cards import TOOL_DESCRIPTION, TOOL_SCHEMA

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1"
WARM_TOKENS = 16
"""The warm-up's reply: some providers refuse fewer."""
MAX_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class Prices:
    """Dollars per token, from OpenRouter's price list, for when a reply doesn't
    report its own cost."""

    prompt: float
    completion: float
    cache_read: float | None = None


type Chunk = Mapping[str, Any]


class ChatClient(Protocol):
    """OpenAI-style chat completions, streamed (a fake in ``tests/fakes``).

    ``stream`` yields each chunk of the reply as parsed JSON. It raises ``BrainError``
    when the request fails: a refused key, no credit, offline."""

    def stream(
        self, body: Mapping[str, object]
    ) -> AbstractAsyncContextManager[AsyncIterator[Chunk]]: ...

    async def close(self) -> None: ...


class OpenRouterClient:
    """``ChatClient`` for OpenRouter, over HTTP."""

    def __init__(self, api_key: str, *, client: httpx2.AsyncClient | None = None) -> None:
        # The session gives up after its own deadline, so no retries here.
        self._http = client or httpx2.AsyncClient(
            base_url=URL, timeout=httpx2.Timeout(8.0, connect=3.0)
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            # Named on OpenRouter's side, so its dashboard says what made the request.
            "X-Title": "Tycho",
            "HTTP-Referer": "https://github.com/benshapyro/tycho-ai",
        }

    @asynccontextmanager
    async def stream(self, body: Mapping[str, object]) -> AsyncGenerator[AsyncIterator[Chunk]]:
        try:
            async with self._http.stream(
                "POST", "/chat/completions", json=dict(body), headers=self._headers
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise BrainError(_explain(response.status_code))
                yield chunks(response.aiter_lines())
        except httpx2.HTTPError as e:
            raise BrainError("can't reach OpenRouter (offline?)") from e

    async def close(self) -> None:
        await self._http.aclose()


def _explain(status: int) -> str:
    if status == 401:
        return "the OpenRouter key was refused"
    if status == 402:
        return "the OpenRouter account is out of credit"
    return f"OpenRouter returned an error ({status})"


async def chunks(lines: AsyncIterator[str]) -> AsyncIterator[Chunk]:
    """The chunks of a server-sent event stream: ``data:`` lines, up to ``[DONE]``.
    Comments (OpenRouter sends ": OPENROUTER PROCESSING" while it waits) are skipped."""
    async for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            log.warning("OpenRouter sent a line that isn't JSON; skipped")
            continue
        if isinstance(chunk, dict):
            yield chunk


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
    generation: str = ""
    """OpenRouter's id for the call, to look its cost up by."""
    _partials: dict[int, _Partial] = field(default_factory=dict[int, _Partial])

    def read(self, chunk: Chunk) -> list[_Partial]:
        """Take in one chunk. Returns the tool calls it completed, in order."""
        if (error := chunk.get("error")) is not None:
            raise BrainError(f"OpenRouter: {_error_text(error)}")
        self.generation = self.generation or str(chunk.get("id") or "")
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
    ) -> None:
        """``model`` is OpenRouter's id ("anthropic/claude-haiku-5.5"). ``effort`` asks a
        model that reasons to reason that hard ("low"); by default nothing is asked, so
        each model runs as its provider sets it. ``prices`` is for replies that don't
        report their cost. The rest is as ``ClaudeBrain``."""
        if client is None and not api_key:
            raise ValueError("an OpenRouter key or a client is needed")
        self._owns_client = client is None
        self._client: ChatClient = client or OpenRouterClient(api_key or "")
        self._matcher = matcher
        self._model = model
        self._today = today
        self._effort = effort
        self._prices = prices
        self._sequences = sorted(c.id for c in matcher.commands if c.is_sequence)
        self._lookups = {lookup.name: lookup for lookup in lookups}
        if clash := ACTS & self._lookups.keys():
            raise ValueError(f"a look-up can't be named {', '.join(sorted(clash))}")
        self._tools = [_function(spec) for spec in tool_specs(self._sequences, lookups)]
        self._reference = reference
        self._about = about
        self._cards = cards
        self._for: Keybinds | None = None
        self._system: dict[str, object] = {}
        self._actions: frozenset[str] = frozenset()
        self._preparing: asyncio.Future[list[None]] | None = None

    async def warm(self, keybinds: Keybinds) -> None:
        """Warm the cache (for models that cache), and load what the look-ups need."""
        if self._preparing is None:
            lookups = self._lookups.values()
            self._preparing = asyncio.gather(*(lookup.prepare() for lookup in lookups))
        await asyncio.gather(self._warm_cache(keybinds), asyncio.shield(self._preparing))

    async def close(self) -> None:
        if self._preparing is not None and not self._preparing.done():
            self._preparing.cancel()
            await asyncio.gather(self._preparing, return_exceptions=True)
        for lookup in self._lookups.values():
            if (close := getattr(lookup, "close", None)) is not None:
                with contextlib.suppress(Exception):
                    await close()
        if self._owns_client:
            await self._client.close()

    async def _warm_cache(self, keybinds: Keybinds) -> None:
        """One short request with the same briefing and tools as ``decide``'s."""
        self._brief(keybinds)
        started = time.perf_counter()
        body = self._body([{"role": "user", "content": "ready"}], max_tokens=WARM_TOKENS)
        try:
            async with self._client.stream(body) as stream:
                usage = None
                async for chunk in stream:
                    usage = chunk.get("usage") or usage
        except BrainError as e:
            log.warning("Couldn't warm up the brain: %s", e)
            return
        self._log_usage(usage, started, "Brain warmed up")

    async def decide(self, request: BrainRequest) -> Decision:
        self._brief(request.keybinds)
        started = time.perf_counter()
        about = await asyncio.to_thread(self._about)
        messages = _messages(request.recent, request.text, self._today(), about)
        turn = Turn(waiting=request.waiting, looking=request.looking, checking=request.checking)
        for call in range(MAX_CALLS):
            decided, message, round_ = await self._call(messages, started, turn)
            if decided is not None:
                if turn.searched and not isinstance(decided, Reply):
                    log.warning("The brain chose to act after a web search; not pressing")
                    return Reply(NO_PRESS_AFTER_SEARCH)
                if isinstance(decided, Reply):
                    return credited(decided, turn.credits)
                return decided
            self._log_usage(message.usage, started)
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
            if note := last_note(call, turn):
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
                    for call in message.read(chunk):
                        if (
                            decided := await self._tool_call(round_, call, message, started)
                        ) is not None:
                            return decided, message, round_
                for call in message.rest():
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
            # The call ends here, so its usage never arrives: the eval looks its cost
            # up by the generation's id.
            log.debug(
                "Brain acted in %.0f ms, generation %s", ms(started), message.generation or "?"
            )
        return decided

    def _body(self, messages: list[dict[str, Any]], max_tokens: int = MAX_TOKENS) -> dict[str, Any]:
        tools = self._tools
        if self._cards():
            tools = [*tools[:2], _function(_SHOW_CARD), *tools[2:]]
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
        if self._effort is not None:
            body["reasoning"] = {"effort": self._effort, "exclude": True}
        return body

    def _brief(self, keybinds: Keybinds) -> None:
        """Rebuild the briefing only when the keybinds change, so the cache holds."""
        if keybinds is self._for:
            return
        self._for = keybinds
        self._actions = frozenset(offered_actions(keybinds, self._matcher))
        text = briefing(keybinds, self._matcher)
        if self._reference:
            text += f"\n{self._reference.strip()}\n"
        # cache_control is for the models that need it marked (Anthropic's, Gemini's);
        # OpenRouter drops it for the rest, which cache by themselves or not at all.
        part = {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
        self._system = {"role": "system", "content": [part]}

    def _log_usage(
        self, usage: Mapping[str, Any] | None, started: float, what: str = "Brain replied"
    ) -> None:
        if not usage:
            log.debug("%s in %.0f ms: no usage reported", what, ms(started))
            return
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
            "%s in %.0f ms: %d in, %d out, %d cached%s",
            what,
            ms(started),
            prompt,
            completion,
            cached,
            priced,
        )


_SHOW_CARD: dict[str, object] = {
    "name": "show_card",
    "description": TOOL_DESCRIPTION,
    "input_schema": dict(TOOL_SCHEMA),
}


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


def _messages(
    recent: Sequence[Exchange], text: str, today: datetime.date, about: str
) -> list[dict[str, Any]]:
    """The same turns ``ClaudeBrain`` sends: its text parts are OpenAI's shape too."""
    return [dict(m) for m in history(recent, text, today, about)]


def _error_text(error: object) -> str:
    if isinstance(error, dict):
        message: object = cast(dict[str, Any], error).get("message")
        if message:
            return str(message)
    return str(cast(object, error))[:200]
