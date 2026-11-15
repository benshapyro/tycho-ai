"""Which ElevenLabs voice Tycho speaks with, and the key it needs (ADR 9). Out of
``app.py`` (#398): ``tycho check`` and starting Tycho share it.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from tycho import paths
from tycho.findings import TYCHO, Finding, stored_key
from tycho.settings import Settings
from tycho.voice.voices import Voice

if TYPE_CHECKING:
    from tycho.runtime import Speaker

log = logging.getLogger("tycho")


VOICE_KEY_HELP = f"{TYCHO} key set elevenlabs"


def elevenlabs_key() -> str | None:
    return stored_key("elevenlabs", "ElevenLabs", VOICE_KEY_HELP)


def choose_voice(current: Settings, key: str) -> tuple[Voice, Finding]:
    """The voice to speak with, asking ElevenLabs (free) whether the player's key can
    use the one they chose; George on Flash if it can't (ADR 9, #242)."""
    from tycho.voice import voices

    chosen = voices.choose(current.voice, current.voice_model)
    checked = voices.check(key, chosen)
    if checked.availability == "missing" and (instead := voices.fallback(chosen)):
        detail = (
            f"{checked.reason}, so Tycho speaks as {instead.describe()}. "
            "Set voice to one your account has."
        )
        return instead, Finding("warn", "Voice", detail, fix=f"voice in {paths.settings_file()}")
    if checked.availability == "available":
        return chosen, Finding("ok", "Voice", f"ElevenLabs, {chosen.describe()}")
    return chosen, Finding(
        "ok", "Voice", f"ElevenLabs, {chosen.describe()} ({checked.reason or 'not checked'})"
    )


voice_now: Finding | None = None
"""The voice the running Tycho speaks with, for the window's status (#242). None until
``load_speaker`` has chosen one."""


def load_speaker(
    current: Settings,
    on_fallback: Callable[[str], None] = lambda _said: None,
    doing: Callable[[str], None] = lambda _now: None,
) -> Speaker | None:
    """ElevenLabs, if the player has stored a key and the voice isn't turned off.
    Without it, Tycho's replies are shown in its window and it plays its sounds.
    ``on_fallback`` is told, in words for the player, when their voice isn't available
    and Tycho speaks as George instead: at start, or later in the session."""
    global voice_now
    if current.voice_volume == 0:
        log.info("Voice: off (voice_volume is 0)")
        return None
    key = elevenlabs_key()
    if key is None:
        log.info(
            "No ElevenLabs key, so Tycho's replies are shown here. Add one: %s", VOICE_KEY_HELP
        )
        return None
    from tycho.voice.elevenlabs import ElevenLabsSpeaker

    doing("Checking your voice")
    voice, finding = choose_voice(current, key)
    voice_now = finding
    if finding.level == "warn":
        log.warning("Voice: %s", finding.detail)
        on_fallback(finding.detail)
    else:
        log.info("Voice: %s", finding.detail)

    def fell_back(gone: Voice, used: Voice) -> None:
        global voice_now
        said = (
            f"{gone.name} isn't available to your ElevenLabs key, so Tycho speaks as "
            f"{used.describe()}. Set voice to one your account has."
        )
        voice_now = Finding("warn", "Voice", said, fix=f"voice in {paths.settings_file()}")
        on_fallback(said)

    return ElevenLabsSpeaker(
        api_key=key,
        voice=voice,
        volume=current.voice_volume,
        kept=paths.voice_dir(),
        on_fallback=fell_back,
    )
