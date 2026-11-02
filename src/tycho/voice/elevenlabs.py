"""ElevenLabs text-to-speech, streamed so Tycho starts talking before the line has
fully arrived (ADR 9, ADR 14).

Audio comes as raw 16-bit PCM at 24 kHz, so nothing needs decoding: each piece
goes straight to the sound card. The connection is opened while the brain is
still thinking, so the request doesn't pay for the handshake.

Lines said again and again (``keep``) are kept as audio files on the player's PC,
one per voice and wording, so they play at once and cost nothing the next time.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx2

from tycho.voice import SpeechError
from tycho.voice.playback import Sink, SoundDeviceSink

log = logging.getLogger(__name__)

API = "https://api.elevenlabs.io"
MODEL = "eleven_v4_turbo"
SHIPS_MIND = "Es2YFCHLnv4SHTRUsaih"
"""The Ship's Mind, designed for Tycho (ADR 9). Other players can use it once it's
shared to ElevenLabs' public Voice Library (#25)."""
GEORGE = "JBFqnCBsd6RMkjVDRZzb"
"""A stock British voice, the closest to The Ship's Mind, used when it isn't available."""
RATE = 24_000
MAX_CHARACTERS = 400
"""The brain is asked for short lines; this caps the cost of a long one."""


class ElevenLabsSpeaker:
    """Satisfies ``runtime.Speaker``, with the player's own ElevenLabs key."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx2.AsyncClient | None = None,
        open_sink: Callable[[int, float], Sink] = SoundDeviceSink,
        voice: str = SHIPS_MIND,
        model: str = MODEL,
        volume: float = 1.0,
        kept: Path | None = None,
    ) -> None:
        """``kept`` is the folder for lines spoken with ``keep``; without one, every
        line is fetched."""
        self._owns_client = client is None
        if client is None:
            if api_key is None:
                raise ValueError("ElevenLabsSpeaker needs an API key or a client")
            client = httpx2.AsyncClient(
                base_url=API,
                headers={"xi-api-key": api_key},
                timeout=httpx2.Timeout(10.0, connect=3.0),
                limits=httpx2.Limits(keepalive_expiry=60.0),
            )
        self._client = client
        self._open_sink = open_sink
        self._voice = voice
        self._model = model
        self._gain = min(1.0, max(0.0, volume)) ** 1.5  # the same curve as the sounds
        self._kept = kept

    @property
    def voice(self) -> str:
        return self._voice

    async def warm(self) -> None:
        """Open the connection, so the next line starts sooner. Never raises."""
        try:
            await self._client.head("/")  # any answer will do; it's the handshake that counts
        except httpx2.HTTPError as e:
            log.debug("Couldn't reach ElevenLabs ahead of time: %s", e)

    async def speak(self, text: str, *, keep: bool = False) -> None:
        """Say ``text``, returning when it has been spoken. Cancelling stops it at once.

        Raises ``SpeechError`` if nothing could be spoken. A connection lost part
        way is logged, and what already arrived is still played.
        """
        text = " ".join(text.split())[:MAX_CHARACTERS]
        if not text:
            return
        file = self._file_for(text) if keep else None
        if file is not None and (audio := await asyncio.to_thread(_read, file)):
            return await self._play(text, audio)
        heard = bytearray() if file is not None else None
        sink: Sink | None = None
        try:
            started = time.perf_counter()
            try:
                async with self._client.stream(
                    "POST",
                    f"/v1/text-to-speech/{self._voice}/stream",
                    params={"output_format": f"pcm_{RATE}"},
                    json={"text": text, "model_id": self._model},
                ) as response:
                    if response.status_code != 200:
                        status, body = response.status_code, await response.aread()
                        if _voice_missing(status, body) and self._voice != GEORGE:
                            log.warning("The Ship's Mind voice isn't available; using George")
                            self._voice = GEORGE
                            sink = None
                        else:
                            raise SpeechError(_explain(status, body))
                    else:
                        sink = self._open(text)
                        async for piece in response.aiter_bytes():
                            if started:
                                log.debug("First audio in %.0f ms", _ms(started))
                                started = 0.0
                            sink.write(piece)
                            if heard is not None:
                                heard += piece
            except httpx2.HTTPError as e:
                if sink is None:
                    raise SpeechError(f"couldn't reach ElevenLabs: {e}") from e
                log.warning("ElevenLabs stopped part way through a line: %s", e)
                heard = None  # only whole lines are kept
            if sink is None:  # the voice was missing: once more, with George
                return await self.speak(text, keep=keep)
            sink.finish()
            if file is not None and heard:
                await asyncio.to_thread(_write, file, bytes(heard))
            await sink.drained()
        finally:
            if sink is not None:
                sink.close()

    async def _play(self, text: str, audio: bytes) -> None:
        sink = self._open(text)
        try:
            sink.write(audio)
            sink.finish()
            await sink.drained()
        finally:
            sink.close()

    def _file_for(self, text: str) -> Path | None:
        if self._kept is None:
            return None
        digest = hashlib.sha256(f"{self._voice}|{self._model}|{text}".encode()).hexdigest()
        return self._kept / f"{digest[:20]}.pcm"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _open(self, text: str) -> Sink:
        try:
            return self._open_sink(RATE, self._gain)
        except Exception as e:  # PortAudio raises its own types
            raise SpeechError(f"couldn't play speech: {e}") from e


def _read(file: Path) -> bytes | None:
    try:
        return file.read_bytes() or None
    except OSError:
        return None


def _write(file: Path, audio: bytes) -> None:
    """Never raises: a line that can't be kept is just fetched again next time."""
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        partial = file.with_suffix(".partial")
        partial.write_bytes(audio)
        os.replace(partial, file)
    except OSError as e:
        log.debug("Couldn't keep a spoken line in %s: %s", file, e)


def _voice_missing(status: int, body: bytes) -> bool:
    return status in (400, 404) and "voice" in _code(body) and "not_found" in _code(body)


def _explain(status: int, body: bytes) -> str:
    """A reason a player can act on. Never includes the key."""
    code = _code(body)
    if status == 401:
        if "quota" in code:
            return "out of ElevenLabs credits for this month"
        return "ElevenLabs didn't accept the key (it needs text-to-speech permission)"
    if status in (402, 403) or "quota" in code:
        return "out of ElevenLabs credits, or the plan doesn't allow this"
    if status == 429:
        return "ElevenLabs is busy or the plan's limit was reached; try again shortly"
    return f"ElevenLabs answered {status}" + (f" ({code})" if code else "")


def _code(body: bytes) -> str:
    """ElevenLabs' error code, e.g. ``quota_exceeded``, or ``""``."""
    try:
        detail = json.loads(body).get("detail", {})
    except ValueError, AttributeError:
        return ""
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("code") or "").lower()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return ""


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
