"""The ElevenLabs speaker, against a mock HTTP transport and a fake sound card (ADR 14)."""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx2
import pytest

from tycho.voice import SpeechError
from tycho.voice.elevenlabs import (
    GEORGE,
    MAX_CHARACTERS,
    RATE,
    SHIPS_MIND,
    ElevenLabsSpeaker,
    _make_room,  # pyright: ignore[reportPrivateUsage]
)


class FakeSink:
    """Satisfies ``voice.playback.Sink``. ``hang`` keeps "playing" until closed."""

    def __init__(self, rate: int, gain: float, *, hang: bool = False) -> None:
        self.rate, self.gain = rate, gain
        self.audio = bytearray()
        self.finished = False
        self.closed = False
        self.playing = asyncio.Event()
        self._hang = hang

    def write(self, pcm: bytes) -> None:
        self.audio += pcm

    def finish(self) -> None:
        self.finished = True
        self.playing.set()

    async def drained(self) -> None:
        if self._hang:
            await asyncio.Event().wait()

    def close(self) -> None:
        self.closed = True


class Rig:
    def __init__(
        self,
        answer: Callable[[httpx2.Request], httpx2.Response],
        *,
        hang: bool = False,
        kept: Path | None = None,
    ) -> None:
        self.requests: list[httpx2.Request] = []
        self.sinks: list[FakeSink] = []
        self.playing = asyncio.Event()
        """Set when any sink has been given its whole line."""

        def handler(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            return answer(request)

        def open_sink(rate: int, gain: float) -> FakeSink:
            sink = FakeSink(rate, gain, hang=hang)
            sink.playing = self.playing
            self.sinks.append(sink)
            return sink

        client = httpx2.AsyncClient(
            base_url="https://api.elevenlabs.io", transport=httpx2.MockTransport(handler)
        )
        self.speaker = ElevenLabsSpeaker(client=client, open_sink=open_sink, volume=0.5, kept=kept)


def _audio(_: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, content=b"\x01\x00" * 2400, headers={"content-type": "audio/pcm"})


def _error(status: int, code: str) -> Callable[[httpx2.Request], httpx2.Response]:
    body = {"detail": {"status": code, "message": "no"}}
    return lambda _: httpx2.Response(status, json=body)


def test_a_line_is_streamed_to_the_sound_card_and_played_to_the_end() -> None:
    rig = Rig(_audio)
    asyncio.run(rig.speaker.speak("Lights off."))
    (request,) = rig.requests
    assert request.url.path == f"/v1/text-to-speech/{SHIPS_MIND}/stream"
    assert request.url.params["output_format"] == f"pcm_{RATE}"
    assert json.loads(request.content) == {"text": "Lights off.", "model_id": "eleven_v4_turbo"}
    (sink,) = rig.sinks
    assert sink.rate == RATE
    assert sink.gain == pytest.approx(0.5**1.5)
    assert len(sink.audio) == 4800
    assert sink.finished
    assert sink.closed


def test_a_long_line_is_cut_and_blank_lines_cost_nothing() -> None:
    rig = Rig(_audio)
    asyncio.run(rig.speaker.speak("word " * 200))
    asyncio.run(rig.speaker.speak("   "))
    (request,) = rig.requests
    assert len(json.loads(request.content)["text"]) == MAX_CHARACTERS


def test_without_the_ships_mind_it_falls_back_to_george() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if SHIPS_MIND in request.url.path:
            return _error(404, "voice_not_found")(request)
        return _audio(request)

    rig = Rig(answer)
    asyncio.run(rig.speaker.speak("Lights off."))
    assert [GEORGE in r.url.path for r in rig.requests] == [False, True]
    assert rig.speaker.voice == GEORGE
    assert len(rig.sinks) == 1


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (401, "quota_exceeded", "out of ElevenLabs credits"),
        (401, "invalid_api_key", "didn't accept the key"),
        (429, "too_many_concurrent_requests", "busy"),
        (500, "", "answered 500"),
    ],
)
def test_a_refusal_says_why_and_plays_nothing(status: int, code: str, reason: str) -> None:
    rig = Rig(_error(status, code))
    with pytest.raises(SpeechError, match=reason):
        asyncio.run(rig.speaker.speak("Lights off."))
    assert rig.sinks == []


def test_no_connection_is_a_speech_error() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    rig = Rig(answer)
    with pytest.raises(SpeechError, match="couldn't reach ElevenLabs"):
        asyncio.run(rig.speaker.speak("Lights off."))


def test_cancelling_stops_the_sound_at_once() -> None:
    rig = Rig(_audio, hang=True)

    async def scenario() -> None:
        task = asyncio.create_task(rig.speaker.speak("A long answer about mining ships."))
        await rig.playing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert rig.sinks[0].closed


def test_warming_up_never_raises() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("offline", request=request)

    asyncio.run(Rig(answer).speaker.warm())


def test_a_kept_line_is_fetched_once_then_played_from_disk(tmp_path: Path) -> None:
    rig = Rig(_audio, kept=tmp_path)
    asyncio.run(rig.speaker.speak("Let me check.", keep=True))
    asyncio.run(rig.speaker.speak("Let me check.", keep=True))
    assert len(rig.requests) == 1
    first, second = rig.sinks
    assert second.audio == first.audio
    assert second.finished
    assert second.closed
    (file,) = tmp_path.iterdir()
    assert file.suffix == ".pcm"


def test_only_kept_lines_are_kept(tmp_path: Path) -> None:
    rig = Rig(_audio, kept=tmp_path)
    asyncio.run(rig.speaker.speak("Gear down."))
    asyncio.run(rig.speaker.speak("Gear down."))
    assert len(rig.requests) == 2
    assert list(tmp_path.iterdir()) == []
    # Without a folder, keep just fetches.
    plain = Rig(_audio)
    asyncio.run(plain.speaker.speak("Let me check.", keep=True))
    asyncio.run(plain.speaker.speak("Let me check.", keep=True))
    assert len(plain.requests) == 2


def test_a_line_cut_off_part_way_isnt_kept(tmp_path: Path) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        class Stream(httpx2.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                yield bytes(200)
                raise httpx2.ReadError("dropped", request=request)

        return httpx2.Response(200, stream=Stream())

    rig = Rig(broken, kept=tmp_path)
    asyncio.run(rig.speaker.speak("Let me check.", keep=True))
    assert list(tmp_path.iterdir()) == []


def test_the_kept_lines_stay_within_their_room_dropping_the_least_used(tmp_path: Path) -> None:
    rig = Rig(_audio, kept=tmp_path)  # each line is 4,800 bytes
    for line in ("One.", "Two.", "Three."):
        asyncio.run(rig.speaker.speak(line, keep=True))
    # Which file holds which line, from the speaker itself: sorting by time can't
    # tell files written in the same instant apart on Windows (#175).
    found = [rig.speaker._file_for(line) for line in ("One.", "Two.", "Three.")]  # pyright: ignore[reportPrivateUsage]
    oldest = [f for f in found if f is not None and f.exists()]
    assert len(oldest) == 3
    for age, file in enumerate(oldest):
        os.utime(file, (1000 + age, 1000 + age))  # One, Two, Three: oldest first
    asyncio.run(rig.speaker.speak("One.", keep=True))  # used again: now the newest
    assert len(rig.requests) == 3

    _make_room(tmp_path, 2 * 4800)
    left = {f.name for f in tmp_path.iterdir()}
    assert len(left) == 2
    assert oldest[0].name in left  # "One." was just used
    assert oldest[1].name not in left  # "Two." was used longest ago
