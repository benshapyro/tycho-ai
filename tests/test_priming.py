"""Getting the brain ready as push-to-talk goes down (#21, ``brain/priming.py``)."""

import asyncio
import contextlib
import json
from typing import Any

import anthropic
import httpx2

from tests.fakes.fake_chat import FakeChat, text
from tests.test_brain import _end, _message_start, _sse  # pyright: ignore[reportPrivateUsage]
from tycho.brain.claude import ClaudeBrain
from tycho.brain.decisions import BrainError, BrainRequest
from tycho.brain.openrouter import OpenRouterBrain
from tycho.brain.priming import CACHE_STALE_SECONDS, IDLE_SECONDS, Primer
from tycho.commands import CommandMatcher
from tycho.keybinds import Keybinds
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)
MATCHER = CommandMatcher.default(pack=SC)
KEYBINDS = Keybinds([], {})


class _Rig:
    def __init__(self, *, rewarmed: bool = True, fails: bool = False, takes: float = 0.0) -> None:
        self.now = 0.0
        self.connects = 0
        self.rewarms = 0
        self.written = 0
        self._rewarmed = rewarmed
        self._fails = fails
        self._takes = takes
        self.primer = Primer(self._connect, self._rewarm, clock=lambda: self.now)

    async def _connect(self) -> None:
        self.connects += 1
        await asyncio.sleep(self._takes)
        if self._fails:
            raise OSError("offline")

    async def _rewarm(self) -> bool:
        self.rewarms += 1
        await asyncio.sleep(self._takes)
        self.written += 1
        return self._rewarmed

    def press(self) -> None:
        """Prime, and let what it started finish."""

        async def go() -> None:
            self.primer.prime()
            await asyncio.sleep(0.01)
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


def test_a_request_waits_for_the_cache_write_a_press_started() -> None:
    """#533: sent alongside the write, the request missed the cache and wrote it again."""
    rig = _Rig(takes=0.05)

    async def go() -> int:
        rig.primer.prime()
        await rig.primer.going()
        return rig.written

    assert asyncio.run(go()) == 1


def test_a_request_waits_for_a_cache_write_only_so_long() -> None:
    rig = _Rig(takes=5.0)

    async def go() -> int:
        rig.primer.prime()
        await rig.primer.going(0.01)
        written = rig.written
        await rig.primer.close()
        return written

    assert asyncio.run(go()) == 0


def test_a_request_doesnt_wait_for_a_connection_being_opened() -> None:
    rig = _Rig(takes=5.0)
    rig.primer.used()
    rig.now += 30

    async def go() -> int:
        rig.primer.prime()
        await asyncio.sleep(0)
        async with asyncio.timeout(1):
            await rig.primer.going()
        await rig.primer.close()
        return rig.connects

    assert asyncio.run(go()) == 1


def test_a_press_during_the_start_up_warm_up_starts_nothing() -> None:
    """#533: the cache was written twice when the player pressed in the first seconds."""
    rig = _Rig()

    async def warm_up() -> None:
        rig.primer.prime()
        await asyncio.sleep(0)

    async def go() -> None:
        await rig.primer.warming(warm_up)
        await rig.primer.close()

    asyncio.run(go())
    assert rig.rewarms == 0


def test_a_request_that_failed_doesnt_count_as_reading_the_cache() -> None:
    """#533: only a request that got its answer renews the cache's hour."""
    rig = _Rig()
    rig.primer.used()
    rig.now += CACHE_STALE_SECONDS + 1
    asyncio.run(rig.primer.going())  # the request went, then failed: no ``used``
    rig.press()
    assert rig.rewarms == 1


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


def _answering_client() -> tuple[anthropic.AsyncAnthropic, list[tuple[str, str]]]:
    """Like ``_client``, but a request gets a one-line reply, streamed."""
    sent: list[tuple[str, str]] = []
    reply = _sse(
        _message_start(),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi."}},
        {"type": "content_block_stop", "index": 0},
        *_end("end_turn"),
    )

    def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.method, request.url.path))
        headers = {"content-type": "text/event-stream"}
        return httpx2.Response(200, content=reply, headers=headers)

    transport = httpx2.MockTransport(answer)
    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test", http_client=httpx2.AsyncClient(transport=transport)
    )
    return client, sent


def test_a_claude_request_leaves_the_connection_open_for_the_next_press(
    monkeypatch: Any,
) -> None:
    """#533: a press soon after a request sends nothing more."""
    client, sent = _answering_client()
    brain = ClaudeBrain(MATCHER, client=client, pack=SC)
    now = [CACHE_STALE_SECONDS * 2]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])

    async def go() -> None:
        await brain.decide(BrainRequest("hello", KEYBINDS))
        now[0] += IDLE_SECONDS - 1
        brain.prime()
        await asyncio.sleep(0.05)
        await brain.close()

    asyncio.run(go())
    assert sent == [("POST", "/v1/messages")]


def test_an_openrouter_request_leaves_the_connection_open_for_the_next_press(
    monkeypatch: Any,
) -> None:
    chat = FakeChat(text("Hi."))
    brain = OpenRouterBrain(MATCHER, model="anthropic/claude-haiku-5.5", client=chat, pack=SC)
    now = [CACHE_STALE_SECONDS * 2]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])

    async def go() -> None:
        await brain.decide(BrainRequest("hello", KEYBINDS))
        now[0] += IDLE_SECONDS - 1
        brain.prime()
        await asyncio.sleep(0)
        await brain.close()

    asyncio.run(go())
    assert (chat.connected, len(chat.requests)) == (0, 1)


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


def test_a_request_waits_for_the_start_up_warm_up_too() -> None:
    """#540: a request in the first seconds wrote the cache a second time alongside the
    start-up warm-up."""
    rig = _Rig()
    seen: list[str] = []

    async def warm_up() -> None:
        await asyncio.sleep(0.05)
        seen.append("written")

    async def go() -> None:
        warming = asyncio.create_task(rig.primer.warming(warm_up))
        await asyncio.sleep(0)
        await rig.primer.going()
        seen.append("asked")
        await warming

    asyncio.run(go())
    assert seen == ["written", "asked"]


def test_a_request_waits_for_the_start_up_warm_up_only_so_long() -> None:
    rig = _Rig()

    async def go() -> bool:
        warming = asyncio.create_task(rig.primer.warming(lambda: asyncio.sleep(5)))
        await asyncio.sleep(0)
        await rig.primer.going(0.01)
        still = not warming.done()
        warming.cancel()
        return still

    assert asyncio.run(go())


def _stale_openrouter(chat: FakeChat, monkeypatch: Any) -> tuple[OpenRouterBrain, list[float]]:
    """An OpenRouter brain whose cache was last read long ago."""
    brain = OpenRouterBrain(MATCHER, model="anthropic/claude-haiku-5.5", client=chat, pack=SC)
    now = [0.0]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])
    brain._primer.used()  # pyright: ignore[reportPrivateUsage]
    now[0] += CACHE_STALE_SECONDS - 60
    return brain, now


def _due(brain: OpenRouterBrain | ClaudeBrain, now: list[float]) -> bool:
    """Whether a press now would write the cache again."""
    return now[0] - brain._primer._cached > CACHE_STALE_SECONDS  # pyright: ignore[reportPrivateUsage]


def test_a_request_cut_off_after_its_answer_started_still_read_the_cache(
    monkeypatch: Any,
) -> None:
    """#540: only a request that ended with an answer renewed the cache's hour, so one
    that timed out mid-answer left the next press to write it again for nothing."""
    chat = FakeChat([*text("Hi")[:1], BrainError("cut off")])
    brain, now = _stale_openrouter(chat, monkeypatch)

    async def go() -> None:
        with contextlib.suppress(BrainError):
            await brain.decide(BrainRequest("hello", KEYBINDS))
        await brain.close()

    asyncio.run(go())
    now[0] += 120
    assert not _due(brain, now)


def test_a_failed_openrouter_request_doesnt_count_as_reading_the_cache(
    monkeypatch: Any,
) -> None:
    """#540: a request refused before any answer read no cache."""
    chat = FakeChat([BrainError("OpenRouter returned an error (500)")])
    brain, now = _stale_openrouter(chat, monkeypatch)

    async def go() -> None:
        with contextlib.suppress(BrainError):
            await brain.decide(BrainRequest("hello", KEYBINDS))
        await brain.close()

    asyncio.run(go())
    now[0] += 120
    assert _due(brain, now)


def test_a_failed_claude_request_doesnt_count_as_reading_the_cache(monkeypatch: Any) -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error"}})

    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
    )
    brain = ClaudeBrain(MATCHER, client=client, pack=SC)
    now = [0.0]
    monkeypatch.setattr("tycho.brain.priming.now", lambda: now[0])
    brain._primer.used()  # pyright: ignore[reportPrivateUsage]
    now[0] += CACHE_STALE_SECONDS + 60

    async def go() -> None:
        with contextlib.suppress(BrainError):
            await brain.decide(BrainRequest("hello", KEYBINDS))
        await brain.close()

    asyncio.run(go())
    assert _due(brain, now)
