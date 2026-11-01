"""Collecting push-to-talk clips from a continuous audio stream, and finding the quiet
parts of them: so Tycho can stop listening as soon as the player has finished, and
transcribe only the speech (#58).

"Quiet" is relative to the clip's own noise floor, the level of its quietest
blocks, so it works on a gated headset and next to a loud fan alike.
"""

import threading
from collections import deque

import numpy as np
from numpy.typing import NDArray

type Samples = NDArray[np.float32]

BLOCK_SECONDS = 0.02
"""Levels are measured over 20 ms blocks, the microphone's own block size."""
QUIET_DB = 10.0
"""A block is quiet if it's less than this far above the noise floor. Speech is
20 to 40 dB above it, even soft consonants at the end of a word."""
MIN_FLOOR = 0.0003
"""About -70 dBFS: the floor used when a gated microphone sends near-silence."""
TRIM_MARGIN_SECONDS = 0.08
"""Kept either side of the speech when trimming, for soft starts and endings."""


def levels(samples: Samples, samplerate: int) -> Samples:
    """The loudness (RMS) of each whole 20 ms block."""
    block = max(round(samplerate * BLOCK_SECONDS), 1)
    count = len(samples) // block
    blocks = samples[: count * block].reshape(count, block)
    return np.sqrt(np.mean(np.square(blocks), axis=1, dtype=np.float32))


def quiet_threshold(block_levels: Samples) -> float:
    """The level under which a block counts as quiet."""
    floor = max(float(np.percentile(block_levels, 10)), MIN_FLOOR)
    return floor * 10 ** (QUIET_DB / 20)


def trailing_quiet_seconds(samples: Samples, samplerate: int) -> float:
    """How long the end of ``samples`` has been quiet."""
    block_levels = levels(samples, samplerate)
    if len(block_levels) == 0:
        return 0.0
    loud = np.flatnonzero(block_levels >= quiet_threshold(block_levels))
    quiet_blocks = len(block_levels) - (int(loud[-1]) + 1 if len(loud) else 0)
    return quiet_blocks * BLOCK_SECONDS


def trim_silence(samples: Samples, samplerate: int, margin: float = TRIM_MARGIN_SECONDS) -> Samples:
    """``samples`` without the quiet before and after the speech, keeping ``margin``
    either side. Unchanged if nothing in it is louder than the noise floor."""
    block_levels = levels(samples, samplerate)
    if len(block_levels) == 0:
        return samples
    loud = np.flatnonzero(block_levels >= quiet_threshold(block_levels))
    if len(loud) == 0:
        return samples
    block = round(samplerate * BLOCK_SECONDS)
    keep = round(samplerate * margin)
    start = max(int(loud[0]) * block - keep, 0)
    end = min((int(loud[-1]) + 1) * block + keep, len(samples))
    return samples[start:end]


class ClipBuffer:
    """Keeps a rolling pre-roll of audio, and collects a clip while recording.

    The pre-roll catches the first syllable when someone starts speaking as they
    press the button. ``feed`` is called from the audio thread; the other
    methods from the control thread.
    """

    def __init__(self, samplerate: int, preroll_seconds: float) -> None:
        self._samplerate = samplerate
        self._preroll: deque[Samples] = deque()
        self._preroll_frames = 0
        self._preroll_limit = round(samplerate * preroll_seconds)
        self._clip: list[Samples] | None = None
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._clip is not None

    def feed(self, block: Samples) -> None:
        with self._lock:
            if self._clip is not None:
                self._clip.append(block)
                return
            self._preroll.append(block)
            self._preroll_frames += len(block)
            while (
                self._preroll
                and self._preroll_frames - len(self._preroll[0]) >= self._preroll_limit
            ):
                self._preroll_frames -= len(self._preroll.popleft())

    def start(self) -> None:
        with self._lock:
            self._clip = list(self._preroll)

    def quiet_seconds(self) -> float:
        """How long the clip being recorded has been quiet at its end (0 if not recording)."""
        with self._lock:
            blocks = list(self._clip or ())
        if not blocks:
            return 0.0
        return trailing_quiet_seconds(np.concatenate(blocks), self._samplerate)

    def stop(self) -> Samples:
        with self._lock:
            clip, self._clip = self._clip or [], None
            self._preroll.clear()
            self._preroll_frames = 0
        if not clip:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(clip)
