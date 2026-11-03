"""Answers kept for a while, so asking twice in a minute doesn't ask the API twice."""

import time
from collections.abc import Callable


class Fresh[T]:
    """Values by key, each used for ``seconds`` after it was kept.

    Expired values are dropped whenever one is kept, so a long session that asks
    about hundreds of things doesn't keep every answer for ever (audit, 2026-11-03).
    """

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._seconds = seconds
        self._clock = clock
        self._kept: dict[str, tuple[float, T]] = {}

    def __len__(self) -> int:
        return len(self._kept)

    def get(self, key: str) -> T | None:
        hit = self._kept.get(key)
        if hit is None or self._clock() - hit[0] >= self._seconds:
            return None
        return hit[1]

    def keep(self, key: str, value: T) -> None:
        now = self._clock()
        self._kept = {k: v for k, v in self._kept.items() if now - v[0] < self._seconds}
        self._kept[key] = (now, value)
