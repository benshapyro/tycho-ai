"""A look-up that answers from a script, for brain tests (satisfies ``brain.lookups.Lookup``)."""

import asyncio
from collections.abc import Mapping

from tycho.brain.lookups import LookupResult


class FakeLookup:
    """Answers every call with ``answer``, or raises it if it's an exception.
    ``hang`` makes every call wait forever, to test the time limit."""

    def __init__(
        self,
        name: str = "ship_lookup",
        answer: str | Exception = '{"name": "Constellation Andromeda", "maker": "RSI"}',
        *,
        hang: bool = False,
        from_the_web: bool = False,
    ) -> None:
        """``from_the_web``: acts like the web search, slow and unchecked."""
        self.from_the_web = from_the_web
        self._name = name
        self._answer = answer
        self._hang = hang
        self.asked: list[Mapping[str, object]] = []
        self.prepared = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Look up a ship's specs. Use it for any number about a ship."

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        self.prepared = True

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        self.asked.append(arguments)
        if self._hang:
            await asyncio.Event().wait()
        if isinstance(self._answer, Exception):
            raise self._answer
        return LookupResult(self._answer)
