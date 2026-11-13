"""The ElevenLabs speaker, against a mock HTTP transport and a fake sound card (ADR 14)."""

import asyncio
import json
import os
import sys
import threading
import types
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from tycho.voice import SpeechError, voices
from tycho.voice.elevenlabs import (
    MAX_CHARACTERS,
    RATE,
    ElevenLabsSpeaker,
    _make_room,  # pyright: ignore[reportPrivateUsage]
)
from tycho.voice.playback import Sink, SoundDeviceSink
from tycho.voice.voices import FLASH, GEORGE, SARAH, SHIPS_MIND, TURBO, Voice


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
        voice: Voice = voices.DEFAULT,
    ) -> None:
        self.requests: list[httpx2.Request] = []
        self.fell_back: list[tuple[Voice, Voice]] = []
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
        self.speaker = ElevenLabsSpeaker(
            client=client,
            open_sink=open_sink,
            volume=0.5,
            kept=kept,
            voice=voice,
            on_fallback=lambda gone, used: self.fell_back.append((gone, used)),
        )


def _audio(_: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, content=b"\x01\x00" * 2400, headers={"content-type": "audio/pcm"})


def _error(status: int, code: str) -> Callable[[httpx2.Request], httpx2.Response]:
    body = {"detail": {"status": code, "message": "no"}}
    return lambda _: httpx2.Response(status, json=body)


def test_the_sound_card_is_opened_off_the_event_loop() -> None:
    """#60: opening the speech stream can take tens of milliseconds."""
    opened_on: list[int] = []
    rig = Rig(_audio)
    open_sink = rig.speaker._open_sink  # pyright: ignore[reportPrivateUsage]

    def recording(rate: int, gain: float) -> Sink:
        opened_on.append(threading.get_ident())
        return open_sink(rate, gain)

    rig.speaker._open_sink = recording  # pyright: ignore[reportPrivateUsage]

    async def scenario() -> int:
        await rig.speaker.speak("Lights off.")
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())
    assert opened_on
    assert loop_thread not in opened_on
    assert rig.sinks[0].closed


class FakeStream:
    """Stands in for ``sounddevice.RawOutputStream``, noting which thread calls it."""

    def __init__(self, **kwargs: Any) -> None:
        self.calls: list[tuple[str, int]] = [("open", threading.get_ident())]
        self.started = threading.Event()
        self.done = threading.Event()
        STREAMS.append(self)

    def start(self) -> None:
        self.calls.append(("start", threading.get_ident()))
        self.started.set()

    def abort(self, ignore_errors: bool = False) -> None:
        self.calls.append(("abort", threading.get_ident()))

    def close(self, ignore_errors: bool = False) -> None:
        self.calls.append(("close", threading.get_ident()))
        self.done.set()


STREAMS: list[FakeStream] = []


class _Stop(Exception):
    pass


def test_the_speech_stream_starts_and_closes_off_the_loop_and_stops_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.SimpleNamespace(RawOutputStream=FakeStream, CallbackStop=_Stop)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    STREAMS.clear()
    sink = SoundDeviceSink(RATE, head_start=0.0)

    async def scenario() -> tuple[int, bytearray]:
        sink.write(b"\x01\x00" * 4800)
        assert await asyncio.to_thread(STREAMS[0].started.wait, 5)  # playing
        sink.close()  # the player pressed push-to-talk
        block = bytearray(b"\xff" * 640)
        with pytest.raises(_Stop):  # the very next block is silence, and the last
            sink._fill(block, 320, None, None)  # pyright: ignore[reportPrivateUsage]
        return threading.get_ident(), block

    loop_thread, block = asyncio.run(scenario())
    assert block == bytearray(640)
    (stream,) = STREAMS
    assert stream.done.wait(5)
    assert [name for name, _ in stream.calls] == ["open", "start", "abort", "close"]
    assert all(thread != loop_thread for name, thread in stream.calls if name != "open")


def test_a_line_is_streamed_to_the_sound_card_and_played_to_the_end() -> None:
    rig = Rig(_audio)
    asyncio.run(rig.speaker.speak("Lights off."))
    (request,) = rig.requests
    # The default is George on Flash v2.5 (ADR 9, amended 8 November 2026; #242).
    assert request.url.path == f"/v1/text-to-speech/{GEORGE}/stream"
    assert request.url.params["output_format"] == f"pcm_{RATE}"
    assert json.loads(request.content) == {"text": "Lights off.", "model_id": FLASH}
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


def test_a_long_line_is_cut_at_a_sentence() -> None:
    """#350: the voice's cap cut mid-word, wherever the 400th character fell."""
    rig = Rig(_audio)
    asyncio.run(rig.speaker.speak("This one fits. " * 30))
    (request,) = rig.requests
    text = json.loads(request.content)["text"]
    assert len(text) <= MAX_CHARACTERS
    assert text.endswith("This one fits.")


def test_a_chosen_voice_is_asked_for_with_its_own_model() -> None:
    rig = Rig(_audio, voice=voices.choose("Sarah"))
    asyncio.run(rig.speaker.speak("Lights off."))
    (request,) = rig.requests
    assert request.url.path == f"/v1/text-to-speech/{SARAH}/stream"
    assert json.loads(request.content)["model_id"] == TURBO


def test_without_the_ships_mind_it_falls_back_to_george_on_flash_and_says_so() -> None:
    def answer(request: httpx2.Request) -> httpx2.Response:
        if SHIPS_MIND in request.url.path:
            return _error(404, "voice_not_found")(request)
        return _audio(request)

    ships_mind = voices.choose("The Ship's Mind")
    rig = Rig(answer, voice=ships_mind)
    asyncio.run(rig.speaker.speak("Lights off."))
    assert [GEORGE in r.url.path for r in rig.requests] == [False, True]
    assert [json.loads(r.content)["model_id"] for r in rig.requests] == [TURBO, FLASH]
    assert rig.speaker.voice == voices.DEFAULT
    assert rig.fell_back == [(ships_mind, voices.DEFAULT)]
    assert len(rig.sinks) == 1


def test_george_missing_is_an_error_not_a_loop() -> None:
    rig = Rig(_error(404, "voice_not_found"))
    with pytest.raises(SpeechError):
        asyncio.run(rig.speaker.speak("Lights off."))
    assert len(rig.requests) == 1
    assert rig.fell_back == []


# --- Choosing the voice (#242) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "model", "expected"),
    [
        ("George", "the voice's own", (GEORGE, FLASH)),
        ("george", "", (GEORGE, FLASH)),
        ("Sarah", "the voice's own", (SARAH, TURBO)),
        ("The Ship's Mind", "", (SHIPS_MIND, TURBO)),
        ("ships-mind", "", (SHIPS_MIND, TURBO)),
        (SARAH, "", (SARAH, TURBO)),  # an ID on the shortlist keeps its model
        ("abcdefghij0123456789", "", ("abcdefghij0123456789", TURBO)),
        ("abcdefghij0123456789", "flash", ("abcdefghij0123456789", FLASH)),
        ("George", "v4 turbo", (GEORGE, TURBO)),
    ],
)
def test_the_voice_setting_names_a_voice_and_its_model(
    setting: str, model: str, expected: tuple[str, str]
) -> None:
    voice = voices.choose(setting, model)
    assert (voice.id, voice.model) == expected


class _Answer:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code, self.text = status, text


def _get(status: int, code: str = "") -> Callable[..., _Answer]:
    body = json.dumps({"detail": {"status": code}}) if code else ""
    asked: list[str] = []

    def get(url: str, **kwargs: Any) -> _Answer:
        asked.append(url)
        assert kwargs["headers"] == {"xi-api-key": "sk_test"}
        return _Answer(status, body)

    get.asked = asked  # pyright: ignore[reportFunctionMemberAccess]
    return get


@pytest.mark.parametrize(
    ("status", "code", "availability"),
    [
        (200, "", "available"),
        (404, "voice_not_found", "missing"),
        (400, "voice_not_found", "missing"),
        (400, "invalid_request", "unknown"),  # not a missing voice: keep it (#246)
        (401, "missing_permissions", "unknown"),  # a key allowed only text-to-speech
        (401, "invalid_api_key", "unknown"),
        (500, "", "unknown"),
    ],
)
def test_the_start_check_asks_for_the_voice_itself(
    status: int, code: str, availability: str
) -> None:
    get = _get(status, code)
    checked = voices.check("sk_test", voices.choose("The Ship's Mind"), get)
    assert checked.availability == availability
    assert get.asked == [f"https://api.elevenlabs.io/v1/voices/{SHIPS_MIND}"]  # pyright: ignore[reportFunctionMemberAccess]


def test_the_start_check_never_raises_on_a_key_that_cant_be_sent() -> None:
    # A zero-width space pasted with the key fails when the header is encoded (#246).
    def get(url: str, **kwargs: Any) -> _Answer:
        kwargs["headers"]["xi-api-key"].encode("ascii")
        return _Answer(200)

    checked = voices.check("sk_test\u200b", voices.DEFAULT, get)
    assert checked.availability == "unknown"
    assert "character" in checked.reason


def test_httpx2_refuses_a_header_with_a_hidden_character() -> None:
    # What the stand-in above and elevenlabs_key() rely on: real headers raise for a
    # zero-width space, before anything is sent (#248, from the #247 review).
    with pytest.raises(UnicodeError):
        httpx2.Request("GET", "https://api.elevenlabs.io/", headers={"xi-api-key": "sk_\u200b"})
    with pytest.raises(UnicodeError):
        httpx2.Headers({"x-api-key": "sk-ant-\u200b"}).raw  # noqa: B018


def test_the_start_check_never_raises_offline() -> None:
    def get(url: str, **kwargs: Any) -> _Answer:
        raise httpx2.ConnectError("offline")

    assert voices.check("sk_test", voices.DEFAULT, get).availability == "unknown"


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
