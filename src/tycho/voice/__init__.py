"""Tycho's voice (ADR 9, ADR 14): what the brain says, spoken.

The ElevenLabs speaker is in ``tycho.voice.elevenlabs`` and isn't imported here,
so loading this package doesn't open the sound system.
"""


class SpeechError(Exception):
    """Nothing could be spoken (no connection, a refused key, no credits), so the
    session plays a sound instead. Raised only before any audio has played."""


__all__ = ["SpeechError"]
