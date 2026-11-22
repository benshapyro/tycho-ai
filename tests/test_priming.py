"""Getting the brain ready as push-to-talk goes down (#21, ``brain/priming.py``)."""

import asyncio
import json
from typing import Any

import anthropic
import httpx2

from tests.fakes.fake_chat import FakeChat
from tycho.brain.claude import ClaudeBrain
from tycho.brain.openrouter import OpenRouterBrain
from tycho.brain.priming import CACHE_STALE_SECONDS, IDLE_SECONDS, Primer
from tycho.commands import CommandMatcher
from tycho.keybinds import Keybinds
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)
MATCHER = CommandMatcher.default(pack=SC)
KEYBINDS = Keybinds([], {})


class _Rig:
    def __init__(self, *, rewarmed: bool = True, fails: bool = False) -> None:
        self.now = 0.0
        self.connects = 0
        self.rewarms = 0
        self._rewarmed = rewarmed
        self._fails = fails
        self.primer = Primer(self._connect, self._rewarm, clock=lambda: self.now)

    async def _connect(self) -> None:
        self.connects += 1
        if self._fails:
            raise OSError("offline")

    async def _rewarm(self) -> bool:
        self.rewarms += 1
        return self._rewarmed

    def press(self) -> None:
        """Prime, and let what it started finish."""

        async def go() -> None:
            self.primer.prime()
            await asyncio.sleep(0)
            await self.primer.close()

        asyncio.run(go())


def test_a_press_soon_after_a_request_does_nothing() -> None:
    rig = _Rig()
    rig.primer.used()
    rig.now += IDLE_SECONDS - 1
    rig.press()
    assert (rig.connects, rig.rewarms) == (0, 0)


def test_a_press_after_the_connection_went_idle_opens_one() -> None:
    """Old code: the request after a pause opened its own connection (DNS, TCP, TLS)."""
    rig = _Rig()
    rig.primer.used()
    rig.now += 30
    rig.press()
    assert (rig.connects, rig.rewarms) == (1, 0)
    rig.press()  # it just did: the connection is open
    assert rig.connects == 1


def test_a_press_after_a_long_break_writes_the_cache_again() -> None:
    """Over the cache's hour, the first request was about 3 s slower (#21)."""
    rig = _Rig()
    rig.primer.used()
    rig.now += CACHE_STALE_SECONDS + 1
    rig.press()
    assert (rig.connects, rig.rewarms) == (0, 1)
    rig.now += 30
    rig.press()  # written again: now only the connection is due
    assert (rig.connects, rig.rewarms) == (1, 1)


def test_a_cache_never_written_is_written_on_a_press() -> None:
    rig = _Rig()
    rig.press()
    assert rig.rewarms == 1


def test_nothing_to_warm_yet_leaves_the_cache_due() -> None:
    rig = _Rig(rewarmed=False)
    rig.press()
    rig.press()
    assert rig.rewarms == 2


def test_a_connection_used_without_the_cache_keeps_the_cache_due() -> None:
    rig = _Rig()
    rig.primer.used(cache=False)
    rig.press()
    assert (rig.connects, rig.rewarms) == (0, 1)


def test_a_failure_is_swallowed_and_tried_again_next_press() -> None:
    rig = _Rig(fails=True)
    rig.primer.used()
    rig.now += 30
    rig.press()
    rig.press()
    assert rig.connects == 2


def test_presses_while_priming_start_nothing_more() -> None:
    rig = _Rig()
    rig.primer.used()
    rig.now += 30

    async def go() -> None:
        rig.primer.prime()
        rig.primer.prime()
        await asyncio.sleep(0)
        await rig.primer.close()

    asyncio.run(go())
    assert rig.connects == 1


def _client() -> tuple[anthropic.AsyncAnthropic, list[tuple[str, str]]]:
    """The real ``AsyncAnthropic``: a free GET for the model, an empty warm-up reply."""
    sent: list[tuple[str, str]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.method, request.url.path))
        if request.method == "GET":
            body: dict[str, Any] = {"type": "model", "id": "claude-haiku-5-5"}
            body |= {"display_name": "Haiku", "created_at": "2026-01-01T00:00:00Z"}
            return httpx2.Response(200, json=body)
        usage = {"input_tokens": 1, "output_tokens": 0}
        message: dict[str, Any] = {"id": "m", "type": "message", "role": "assistant", "content": []}
        message |= {"model": "claude-haiku-5-5", "stop_reason": "max_tokens", "usage": usage}
        return httpx2.Response(200, content=json.dumps(message))

    transport = httpx2.MockTransport(answer)
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test", http_client=httpx2.AsyncClient(transport=transport)
    )
    return client, sent


def test_claude_opens_its_connection_with_a_free_request(monkeypatch: Any) -> None:
    client, sent = _client()
    brain = ClaudeBrain(MATCHER, client=client, pack=SC)
    now = [0.0]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])

    async def go() -> None:
        await brain.warm(KEYBINDS)
        now[0] += 30
        brain.prime()
        await asyncio.sleep(0.05)
        await brain.close()

    asyncio.run(go())
    assert sent == [("POST", "/v1/messages"), ("GET", "/v1/models/claude-haiku-5-5")]


def test_claude_writes_its_cache_again_after_a_long_break(monkeypatch: Any) -> None:
    client, sent = _client()
    brain = ClaudeBrain(MATCHER, client=client, pack=SC)
    now = [0.0]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])

    async def go() -> None:
        await brain.warm(KEYBINDS)
        now[0] += CACHE_STALE_SECONDS + 1
        brain.prime()
        await asyncio.sleep(0.05)
        await brain.close()

    asyncio.run(go())
    assert sent == [("POST", "/v1/messages"), ("POST", "/v1/messages")]


def test_openrouter_opens_its_connection_too(monkeypatch: Any) -> None:
    chat = FakeChat()
    brain = OpenRouterBrain(MATCHER, model="anthropic/claude-haiku-5.5", client=chat, pack=SC)
    now = [0.0]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])

    async def go() -> None:
        brain.prime()  # never warmed: nothing briefed, so nothing to write
        await asyncio.sleep(0)
        brain._primer.used()  # pyright: ignore[reportPrivateUsage]
        now[0] += 30
        brain.prime()
        await asyncio.sleep(0)
        await brain.close()

    asyncio.run(go())
    assert chat.connected == 1
