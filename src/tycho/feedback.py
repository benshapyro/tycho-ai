"""The done and not-done sounds, until Tycho can speak (ADR 11).

Both are generated here, so there are no sound files or licences. They're short
and quiet on purpose: they confirm, they don't announce.
- Done: a radio-style click, a crisp blip with a breath of static.
- Not done: a soft, low, falling tone (didn't understand, refused, no key, or
  the game wasn't in front).
"""

import logging
from typing import Protocol

import numpy as np

from tycho.audio.clip import Samples

log = logging.getLogger(__name__)

SAMPLERATE = 48_000
LOUDEST = 0.5
"""Peak level at full volume. Comfortably below clipping, and below the game's own sounds."""


def done_sound(rate: int = SAMPLERATE) -> Samples:
    """About 70 ms: a 15 ms burst of soft static, then a quick 1.9 kHz blip."""
    noise = np.random.default_rng(7).uniform(-1, 1, _frames(0.015, rate))
    noise *= np.linspace(1, 0, len(noise)) ** 2 * 0.35
    t = _time(0.055, rate)
    blip = np.sin(2 * np.pi * 1_900 * t) * np.exp(-t / 0.012)
    return _finish(np.concatenate([noise, blip]), rate)


def not_done_sound(rate: int = SAMPLERATE) -> Samples:
    """About 220 ms: a tone gliding down from 330 to 220 Hz, with a soft start and end."""
    t = _time(0.22, rate)
    frequency = np.linspace(330, 220, len(t))
    phase = 2 * np.pi * np.cumsum(frequency) / rate
    tone = np.sin(phase) + 0.25 * np.sin(2 * phase)  # a little body, so it isn't a pure beep
    envelope = np.minimum(1, t / 0.02) * np.minimum(1, (t[-1] - t) / 0.08)
    return _finish(tone * envelope, rate)


class Player(Protocol):
    """Plays a sound and returns at once."""

    def play(self, samples: Samples, rate: int) -> None: ...


class SoundDevicePlayer:
    """Plays on the Windows default output, without waiting. Satisfies ``Player``."""

    def play(self, samples: Samples, rate: int) -> None:
        import sounddevice as sd  # pyright: ignore[reportMissingTypeStubs] - no type information

        sd.play(samples, rate)  # pyright: ignore[reportUnknownMemberType]


class SoundFeedback:
    """Satisfies ``runtime.Feedback``. A sound failing to play is logged, never raised."""

    def __init__(self, player: Player, volume: float, rate: int = SAMPLERATE) -> None:
        self._player = player
        self._rate = rate
        gain = LOUDEST * min(1.0, max(0.0, volume)) ** 1.5  # gentler at the low end of the slider
        self._done = (done_sound(rate) * gain).astype(np.float32)
        self._not_done = (not_done_sound(rate) * gain).astype(np.float32)
        self._silent = gain == 0

    def done(self) -> None:
        self._play(self._done)

    def not_done(self) -> None:
        self._play(self._not_done)

    def _play(self, samples: Samples) -> None:
        if self._silent:
            return
        try:
            self._player.play(samples, self._rate)
        except Exception as e:  # PortAudio raises its own types; a sound must never stop the loop
            log.warning("Couldn't play a feedback sound: %s", e)


def _frames(seconds: float, rate: int) -> int:
    return round(seconds * rate)


def _time(seconds: float, rate: int) -> np.ndarray[tuple[int], np.dtype[np.float64]]:
    return np.arange(_frames(seconds, rate)) / rate


def _finish(sound: np.ndarray[tuple[int], np.dtype[np.float64]], rate: int) -> Samples:
    """Normalise to a peak of 1, and fade the last 5 ms so it never ends on a click."""
    fade = min(len(sound), _frames(0.005, rate))
    sound[-fade:] *= np.linspace(1, 0, fade)
    return (sound / np.max(np.abs(sound))).astype(np.float32)
