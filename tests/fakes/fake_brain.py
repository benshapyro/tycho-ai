"""A brain that follows a script, for runtime tests (satisfies ``runtime.Brain``)."""

import asyncio

from tycho.brain import BrainRequest, Decision
from tycho.game.keybinds import Keybinds


class FakeBrain:
    """Answers each request with the next scripted decision, or raises it if it's an
    exception. ``hang`` makes every call wait forever, to test the deadline."""

    def __init__(self, *script: Decision | Exception, hang: bool = False) -> None:
        self._script = list(script)
        self._hang = hang
        self.asked: list[BrainRequest] = []
        self.warmed = 0

    async def warm(self, keybinds: Keybinds) -> None:
        self.warmed += 1

    async def decide(self, request: BrainRequest) -> Decision:
        self.asked.append(request)
        if self._hang:
            await asyncio.Event().wait()
        answer = self._script.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
