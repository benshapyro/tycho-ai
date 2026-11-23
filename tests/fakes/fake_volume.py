"""The game's volume in Windows' mixer, as a fake (satisfies ``voice.ducking.GameVolume``)."""

from collections.abc import Mapping

from tycho.voice.ducking import ALL, Levels


class FakeGameVolume:
    """``sliders`` are the game's sliders in the mixer (one, ``"game"``, unless given);
    ``level`` is the first. ``running`` False means the game has no sound session.
    ``levels`` records every level the first slider was set to, in order."""

    def __init__(
        self,
        level: float = 1.0,
        *,
        running: bool = True,
        sliders: Mapping[str, float] | None = None,
    ) -> None:
        self.sliders: Levels = dict(sliders) if sliders else {"game": level}
        self.running = running
        self.levels: list[float] = []

    @property
    def level(self) -> float:
        return next(iter(self.sliders.values()))

    @level.setter
    def level(self, value: float) -> None:
        self.sliders[next(iter(self.sliders))] = value

    def get(self) -> Levels:
        return dict(self.sliders) if self.running else {}

    def set(self, levels: Mapping[str, float]) -> bool:
        if not self.running:
            return False
        found = False
        for i, name in enumerate(self.sliders):
            level = levels.get(name, levels.get(ALL))
            if level is None:
                continue
            self.sliders[name] = level
            found = True
            if i == 0:
                self.levels.append(level)
        return found
