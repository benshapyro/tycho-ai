"""Collecting push-to-talk clips from a continuous audio stream."""

import threading
from collections import deque

import numpy as np
from numpy.typing import NDArray

type Samples = NDArray[np.float32]


class ClipBuffer:
    """Keeps a rolling pre-roll of audio, and collects a clip while recording.

    The pre-roll catches the first syllable when someone starts speaking as they
    press the button. ``feed`` is called from the audio thread; the other
    methods from the control thread.
    """

    def __init__(self, samplerate: int, preroll_seconds: float) -> None:
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

    def stop(self) -> Samples:
        with self._lock:
            clip, self._clip = self._clip or [], None
            self._preroll.clear()
            self._preroll_frames = 0
        if not clip:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(clip)
