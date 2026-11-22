"""OpenRouter over HTTP, for ``openrouter``'s brain (#192): the chat client's
boundary (``ChatClient``, a fake in ``tests/fakes``), the real one, and reading its
streamed chunks and its errors."""

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, cast

import httpx2

from tycho.brain.decisions import BrainError

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1"
READ_SECONDS = 30.0
"""How long to wait for the next byte. Some models take over 8 s to their first token
(Gemini, #268); the session's own deadline still gives up first in play."""


type Chunk = Mapping[str, Any]


class ChatClient(Protocol):
    """OpenAI-style chat completions, streamed (a fake in ``tests/fakes``).

    ``stream`` yields each chunk of the reply as parsed JSON. It raises ``BrainError``
    when the request fails: a refused key, no credit, offline."""

    def stream(
        self, body: Mapping[str, object]
    ) -> AbstractAsyncContextManager[AsyncIterator[Chunk]]: ...

    async def connect(self) -> None:
        """Open a connection ahead of a request, with a free one (``priming``)."""
        ...

    async def close(self) -> None: ...


class OpenRouterClient:
    """``ChatClient`` for OpenRouter, over HTTP."""

    def __init__(self, api_key: str, *, client: httpx2.AsyncClient | None = None) -> None:
        # The session gives up after its own deadline, so no retries here.
        self._http = client or httpx2.AsyncClient(
            base_url=URL, timeout=httpx2.Timeout(READ_SECONDS, connect=3.0)
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
                    raise BrainError(_explain(response.status_code, await response.aread()))
                yield chunks(response.aiter_lines())
        except (httpx2.ReadTimeout, httpx2.WriteTimeout) as e:
            # Only a model that's slow to answer: a connect or pool timeout is a network
            # that drops packets, which is "can't reach" below (#270).
            raise BrainError(f"OpenRouter was too slow to answer ({e.__class__.__name__})") from e
        except httpx2.HTTPError as e:
            raise BrainError("can't reach OpenRouter (offline?)") from e

    async def connect(self) -> None:
        (await self._http.get("/key", headers=self._headers, timeout=3.0)).raise_for_status()

    async def close(self) -> None:
        await self._http.aclose()


def _explain(status: int, body: bytes = b"") -> str:
    if status == 401:
        return "the OpenRouter key was refused"
    if status == 402:
        return "the OpenRouter account is out of credit"
    problem = f"OpenRouter returned an error ({status})"
    if said := _said(body):
        problem += f": {said}"
    return problem


SAID_CHARACTERS = 200


def _said(body: bytes) -> str:
    """OpenRouter's own reason for an error (#268): "ZDR violation (account settings)"
    said why every call failed, and a bare status hid it. It holds no key."""
    try:
        parsed: object = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    error = cast("dict[str, object]", parsed).get("error")
    if not isinstance(error, dict):
        return ""
    said = cast("dict[str, object]", error).get("message")
    if not isinstance(said, str):
        return ""
    message = " ".join(said.split())
    return message[:SAID_CHARACTERS]


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
