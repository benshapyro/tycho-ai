"""A brain that follows a script, for runtime tests (satisfies ``runtime.Brain``)."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from tycho.brain import Decision, Exchange
from tycho.game.keybinds import Keybinds


@dataclass(frozen=True, slots=True)
class Asked:
    text: str
    recent: tuple[Exchange, ...]


class FakeBrain:
    """Answers each request with the next scripted decision, or raises it if it's an
    exception. ``hang`` makes every call wait forever, to test the deadline."""

    def __init__(self, *script: Decision | Exception, hang: bool = False) -> None:
        self._script = list(script)
        self._hang = hang
        self.asked: list[Asked] = []
        self.warmed = 0

    async def warm(self, keybinds: Keybinds) -> None:
        self.warmed += 1

    async def decide(self, text: str, recent: Sequence[Exchange], keybinds: Keybinds) -> Decision:
        self.asked.append(Asked(text, tuple(recent)))
        if self._hang:
            await asyncio.Event().wait()
        answer = self._script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
