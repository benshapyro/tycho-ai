"""The brain on Claude Haiku 5.5 (ADR 6, ADR 14). Satisfies ``runtime.Brain``.

Speed matters most (#21): Tycho acts as soon as the tool call is complete, and
stops reading the stream there, so it never waits for the rest of the answer.
At most one tool call per request, so "the moment it's complete" is well defined.

The briefing (about 11,000 tokens) is cached for an hour, because play is bursty:
gaps of 5 to 60 minutes are normal, and a cold cache added about 3 s. One warm-up
request at startup writes it, so the first real request is fast too.
"""

import datetime
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import anthropic
from anthropic.types import MessageParam, TextBlockParam, ToolParam

from tycho.brain.briefing import briefing, offered_actions
from tycho.brain.decisions import BrainError, Decision, Exchange, Reply, parse_tool_call
from tycho.commands import CommandMatcher
from tycho.game.keybinds import Keybinds

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
EFFORT = "low"
"""97% on the 82-case eval at about 1.2 s; medium scored the same, slower (ADR 6)."""
MAX_TOKENS = 1024
"""Replies are a sentence or two and tool calls a few dozen tokens; this is headroom."""
# Haiku 5.5 per million tokens: input, output, cache read, cache write (1 hour).
_PRICES = (0.10, 0.50, 0.01, 0.20)


def _tools(sequences: list[str]) -> list[ToolParam]:
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
    ]


def history(
    recent: Sequence[Exchange], text: str, today: datetime.date | None = None
) -> list[MessageParam]:
    """Earlier exchanges as turns, then this request, headed by today's date. The
    date lives here, not in the briefing, so the cached briefing never changes."""
    messages: list[MessageParam] = []
    for exchange in recent:
        messages.append({"role": "user", "content": exchange.said})
        messages.append({"role": "assistant", "content": f"[Tycho] {exchange.outcome}"})
    if today is None:
        messages.append({"role": "user", "content": text})
    else:
        date = f"[{today.day} {today:%B %Y}]"
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": date}, {"type": "text", "text": text}],
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
    ) -> None:
        # The session gives up after its own deadline; one quick retry fits inside it.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=anthropic.Timeout(8.0, connect=3.0), max_retries=1
        )
        self._matcher = matcher
        self._model = model
        self._today = today
        self._sequences = sorted(c.id for c in matcher.commands if c.is_sequence)
        self._tools = _tools(self._sequences)
        self._for: Keybinds | None = None
        self._system: list[TextBlockParam] = []
        self._actions: frozenset[str] = frozenset()

    async def warm(self, keybinds: Keybinds) -> None:
        """Write the briefing to the cache and open the connection, billing no output.

        Everything up to the briefing must match ``decide``'s request, or the cache misses.
        """
        self._brief(keybinds)
        started = time.perf_counter()
        try:
            message = await self._client.messages.create(
                model=self._model,
                system=self._system,
                tools=self._tools,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                output_config={"effort": EFFORT},
                max_tokens=0,
                messages=[{"role": "user", "content": "ready"}],
            )
        except anthropic.APIError as e:
            log.warning("Couldn't warm up the brain: %s", e)
            return
        _log_usage(message.usage, started, "Brain warmed up")

    async def decide(self, text: str, recent: Sequence[Exchange], keybinds: Keybinds) -> Decision:
        self._brief(keybinds)
        started = time.perf_counter()
        try:
            async with self._client.messages.stream(
                model=self._model,
                system=self._system,
                tools=self._tools,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                output_config={"effort": EFFORT},
                max_tokens=MAX_TOKENS,
                messages=history(recent, text, self._today()),
            ) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_stop"
                        and event.content_block.type == "tool_use"
                    ):
                        block = event.content_block
                        log.debug("Brain chose %s in %.0f ms", block.name, _ms(started))
                        # Leaving the stream here ends the request: nothing more to wait for.
                        return parse_tool_call(
                            block.name, block.input, self._actions, self._sequences
                        )
                message = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise BrainError("the Anthropic key was refused") from e
        except anthropic.APIConnectionError as e:
            raise BrainError("can't reach Claude (offline?)") from e
        except anthropic.APIStatusError as e:
            raise BrainError(f"Claude returned an error ({e.status_code})") from e
        _log_usage(message.usage, started)
        if message.stop_reason == "refusal":
            raise BrainError("Claude declined")
        reply = " ".join(b.text for b in message.content if b.type == "text").strip()
        if not reply:
            raise BrainError(f"no answer (stopped: {message.stop_reason})")
        return Reply(reply[:400])

    def _brief(self, keybinds: Keybinds) -> None:
        """Rebuild the briefing only when the keybinds change, so the cache holds."""
        if keybinds is self._for:
            return
        self._for = keybinds
        self._actions = frozenset(offered_actions(keybinds, self._matcher))
        text = briefing(keybinds, self._matcher)
        self._system = [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
        ]


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
