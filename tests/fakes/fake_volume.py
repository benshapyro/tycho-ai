"""The game's volume in Windows' mixer, as a fake (satisfies ``voice.ducking.GameVolume``)."""


class FakeGameVolume:
    """``level`` is the slider; ``running`` False means the game has no sound session.
    ``levels`` records every level set, in order."""

    def __init__(self, level: float = 1.0, *, running: bool = True) -> None:
        self.level = level
        self.running = running
        self.levels: list[float] = []

    def get(self) -> float | None:
        return self.level if self.running else None

    def set(self, level: float) -> bool:
        if not self.running:
            return False
        self.level = level
        self.levels.append(level)
        return True
