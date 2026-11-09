"""An OpenAI-style streaming chat client from a script, for the OpenRouter brain
(satisfies ``brain.openrouter.ChatClient``)."""

import json
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from tycho.brain.decisions import BrainError

type Chunk = Mapping[str, Any]


class FakeChat:
    """Answers each call with the next scripted list of chunks. A chunk that is an
    exception is raised there instead, so a test can show the stream was left before
    it. ``read`` counts the chunks handed out, call by call."""

    def __init__(self, *calls: Sequence[Chunk | Exception]) -> None:
        self.requests: list[dict[str, Any]] = []
        self.read: list[int] = []
        self.closed = False
        self._script = list(calls)

    @asynccontextmanager
    async def stream(self, body: Mapping[str, object]) -> AsyncGenerator[AsyncIterator[Chunk]]:
        # Copy the messages: the brain appends to the same list between calls.
        messages = body["messages"]
        assert isinstance(messages, list)
        self.requests.append({**body, "messages": list(messages)})  # pyright: ignore[reportUnknownArgumentType]
        if not self._script:
            raise BrainError("the fake has no more replies")
        script = self._script.pop(0)
        self.read.append(0)

        async def chunks() -> AsyncIterator[Chunk]:
            for chunk in script:
                if isinstance(chunk, Exception):
                    raise chunk
                self.read[-1] += 1
                yield chunk

        yield chunks()

    async def close(self) -> None:
        self.closed = True


def text(*pieces: str, finish: str = "stop", cost: float | None = 0.0001) -> list[Chunk]:
    """A reply in words, in pieces, then its finish and usage."""
    return [
        *({"id": "gen-1", "choices": [{"index": 0, "delta": {"content": p}}]} for p in pieces),
        {"id": "gen-1", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        usage(cost),
    ]


def usage(cost: float | None = 0.0001) -> Chunk:
    counts: dict[str, Any] = {
        "prompt_tokens": 1000,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 800},
    }
    if cost is not None:
        counts["cost"] = cost
    return {"id": "gen-1", "choices": [], "usage": counts}


def tool_call(
    name: str, arguments: Mapping[str, object] | str, *, index: int = 0, use_id: str = "call_1"
) -> list[Chunk]:
    """One tool call, streamed as OpenAI does: its id and name, then its arguments in
    two pieces."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    half = len(raw) // 2
    first = {"index": index, "id": use_id, "type": "function", "function": {"name": name}}
    return [
        {"id": "gen-1", "choices": [{"index": 0, "delta": {"tool_calls": [first]}}]},
        *(
            {
                "id": "gen-1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [{"index": index, "function": {"arguments": p}}]},
                    }
                ],
            }
            for p in (raw[:half], raw[half:])
            if p
        ),
    ]


def finished(reason: str = "tool_calls") -> list[Chunk]:
    return [
        {"id": "gen-1", "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
        usage(),
    ]
