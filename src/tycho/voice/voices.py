"""Which ElevenLabs voice Tycho speaks with, and its model (ADR 9, amended 8 November 2026).

The ``voice`` setting is a shortlist name ("George", "Sarah") or any ElevenLabs voice
ID. Each voice carries its own model: George is on Flash v2.5, Sarah and the rest on
v4 Turbo (Flash rushed Sarah's lines in the listening test). ``voice_model`` can set
another.

A designed voice (The Ship's Mind) belongs to the account that made it: ElevenLabs
doesn't let designed voices into the public Voice Library, so for any other player's
key it's missing. Tycho asks ElevenLabs at start (``check``, free, no characters used)
and falls back to George on Flash.

Nothing here opens the sound system or a connection unless ``check`` is called, so
settings can import it.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

import httpx2

API = "https://api.elevenlabs.io"
TURBO = "eleven_v4_turbo"
FLASH = "eleven_flash_v2_5"
MODEL_NAMES = {TURBO: "v4 Turbo", FLASH: "Flash v2.5"}
CHECK_SECONDS = 5.0

GEORGE = "JBFqnCBsd6RMkjVDRZzb"
SARAH = "EXAVITQu4vr4xnSDxMaL"
LILY = "pFZP5JQG7iQjIQuC4Bku"
BRIAN = "nPczCjzI2devNBz1zQrb"
SHIPS_MIND = "Es2YFCHLnv4SHTRUsaih"
"""Designed for Tycho; only its owner's key can use it (ADR 9 amendment)."""

VOICE_ID = re.compile(r"[A-Za-z0-9]{20}")
"""ElevenLabs voice IDs are 20 letters and digits."""


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    model: str = TURBO
    own_account: bool = False
    """A designed voice: only the account that made it can use it."""

    def describe(self) -> str:
        return f"{self.name} on {MODEL_NAMES.get(self.model, self.model)}"


SHORTLIST: dict[str, Voice] = {
    "george": Voice(GEORGE, "George", FLASH),
    "sarah": Voice(SARAH, "Sarah"),
    "lily": Voice(LILY, "Lily"),
    "brian": Voice(BRIAN, "Brian"),
    "ships mind": Voice(SHIPS_MIND, "The Ship's Mind", own_account=True),
}
"""Keyed by ``_key``: lower case, without "the", apostrophes or hyphens."""
DEFAULT = SHORTLIST["george"]
"""The default voice overall and the default male voice; also the fallback."""


def _key(text: str) -> str:
    words = text.lower().replace("'", "").replace("\u2019", "").replace("-", " ").split()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def is_voice(text: str) -> bool:
    """A shortlist name or something shaped like a voice ID."""
    return _key(text) in SHORTLIST or VOICE_ID.fullmatch(text.strip()) is not None


def by_voice(said: str) -> Voice | None:
    """The shortlist voice a player named aloud ("switch to Sarah"), or None. Only the
    ones every key can use: a voice ID can't be said, and The Ship's Mind works only
    with its owner's key (#355)."""
    voice = SHORTLIST.get(_key(said))
    return voice if voice is not None and not voice.own_account else None


SPOKEN = tuple(v.name for v in SHORTLIST.values() if not v.own_account)
"""The voices a player can pick aloud: George, Sarah, Lily and Brian."""


MODEL_SETTINGS = {"v4 turbo": TURBO, "flash": FLASH}
"""``voice_model``'s choices; any other (the voice's own) keeps the voice's model."""


def choose(setting: str, model: str = "") -> Voice:
    """The voice the settings ask for. ``model`` (``voice_model``), if it names a model,
    replaces the voice's own. An ID that's on the shortlist gets its name and model."""
    text = setting.strip()
    voice = SHORTLIST.get(_key(text))
    if voice is None:
        voice = next((v for v in SHORTLIST.values() if v.id == text), None)
    if voice is None:
        voice = Voice(text, f"your voice {text}")
    chosen = MODEL_SETTINGS.get(model)
    return replace(voice, model=chosen) if chosen else voice


type Availability = Literal["available", "missing", "unknown"]


@dataclass(frozen=True)
class Checked:
    availability: Availability
    reason: str = ""
    """Why it's missing or couldn't be checked, in plain words."""


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...


type Get = Callable[..., _Response]


def check(key: str, voice: Voice, get: Get = httpx2.get) -> Checked:
    """Whether ``key`` can use ``voice``: ``GET /v1/voices/{id}``, which is free. Never
    raises. "unknown" (offline, or a key allowed only text-to-speech) keeps the voice:
    the first line falls back on its own if it really is missing."""
    try:
        response = get(
            f"{API}/v1/voices/{voice.id}", headers={"xi-api-key": key}, timeout=CHECK_SECONDS
        )
    except httpx2.HTTPError as e:
        return Checked("unknown", f"couldn't reach ElevenLabs ({type(e).__name__})")
    except UnicodeError:  # a key with a pasted invisible character can't go in a header
        return Checked("unknown", "your ElevenLabs key has a character that can't be sent")
    status, code = response.status_code, _code(response.text)
    if status == 200:
        return Checked("available")
    if status == 404 or missing(status, response.text):
        return Checked("missing", f"{voice.name} isn't available to your ElevenLabs key")
    if status in (401, 403) and "permission" in code:
        return Checked("unknown", "your key isn't allowed to read voices, so it wasn't checked")
    if status in (401, 403):
        return Checked("unknown", "ElevenLabs refused the key")
    return Checked("unknown", f"ElevenLabs answered {status}" + (f" ({code})" if code else ""))


def fallback(voice: Voice) -> Voice | None:
    """What to use when ``voice`` is missing: George on Flash, unless it's George."""
    return None if voice.id == DEFAULT.id else DEFAULT


def missing(status: int, body: bytes | str) -> bool:
    """A text-to-speech answer that means the voice isn't there for this key."""
    code = _code(body)
    return status in (400, 404) and "voice" in code and "not_found" in code


def _code(body: bytes | str) -> str:
    """ElevenLabs' error code, e.g. ``voice_not_found``, or ``""``."""
    try:
        detail = json.loads(body).get("detail", {})
    except ValueError, AttributeError:
        return ""
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("code") or "").lower()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return ""
