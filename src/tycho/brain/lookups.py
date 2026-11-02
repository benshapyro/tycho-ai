"""Look-ups the brain can make before it answers (ADR 15, #54).

The brain's tools are of two kinds. An *act* (``run_actions``, ``run_sequence``)
ends the request at once and becomes commands the safety gate checks. A *look-up*
fetches something (a ship's specs, a price), its result goes back to Claude, and
Claude carries on. A result is only ever something to say: it can never press a
key, because presses come only from acts, checked against the player's own bindings.

Each look-up is a ``Lookup``, a boundary like any other (a fake in ``tests/fakes``).
Its result is untrusted text from outside, so it goes to Claude only as a tool result,
never into the briefing or the player's turn.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

LOOKUP_SECONDS = 2.5
"""How long one look-up may take before Claude is told it timed out."""


@dataclass(frozen=True, slots=True)
class LookupResult:
    content: str
    """What Claude reads: compact JSON, with the source and how fresh it is (ADR 15)."""
    is_error: bool = False


class Lookup(Protocol):
    """One look-up tool. ``input_schema`` must suit strict tool use: every object
    lists its properties and sets ``additionalProperties`` to false."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str:
        """What it finds, when to use it, and when not to."""
        ...

    @property
    def input_schema(self) -> Mapping[str, object]: ...

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        """Raise nothing you expect: a miss is a result that says so, with what to try."""
        ...


def failure(problem: str) -> LookupResult:
    return LookupResult(json.dumps({"error": problem}), is_error=True)


async def run_lookup(
    lookup: Lookup | None, arguments: object, seconds: float = LOOKUP_SECONDS
) -> LookupResult:
    """Run one look-up within its time limit. Never raises: anything that goes wrong
    becomes an error result, so Claude can answer without it or try something else."""
    if lookup is None:
        return failure("there's no look-up by that name")
    if not isinstance(arguments, dict):
        return failure("the arguments must be an object")
    args: dict[str, object] = {str(k): v for k, v in arguments.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    try:
        return await asyncio.wait_for(lookup.run(args), seconds)
    except TimeoutError:
        log.warning("Look-up %s took longer than %.1f s", lookup.name, seconds)
        return failure("the look-up took too long; answer without it")
    except Exception as e:  # a broken look-up must never break the request
        log.exception("Look-up %s failed", lookup.name)
        return failure(f"the look-up failed ({type(e).__name__}); answer without it")
