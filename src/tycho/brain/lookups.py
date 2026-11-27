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
from typing import Protocol, cast

log = logging.getLogger(__name__)

LOOKUP_SECONDS = 2.5
"""How long one look-up may take before Claude is told it timed out."""


@dataclass(frozen=True, slots=True)
class LookupResult:
    content: str
    """What Claude reads: compact JSON, with the source and how fresh it is (ADR 15)."""
    is_error: bool = False


class Lookup(Protocol):
    """One look-up tool. ``input_schema`` stays strict-shaped (every object lists its
    properties and sets ``additionalProperties`` to false), though it's sent non-strict:
    only the acts are strict (``claude.STRICT``, #638).

    A look-up may also set, read with ``getattr`` (so spell them exactly):
    ``seconds`` (its time limit), ``from_the_web`` (slow and unchecked: a filler line,
    and no pressing after it), ``instant`` (answers at once: no "Checking.", and it
    runs alongside an act), ``credit`` (the source a spoken answer must name when
    it used the look-up's data, "UEX") and ``close`` (an async method the brain awaits
    as it closes, to stop work still running: it may be called more than once)."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str:
        """What it finds, when to use it, and when not to."""
        ...

    @property
    def input_schema(self) -> Mapping[str, object]: ...

    async def prepare(self) -> None:
        """Load or download what the look-up needs. Called once at startup, in the
        background; until it's done, ``run`` says the data isn't ready."""
        ...

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


NO_PRESS_AFTER_SEARCH = (
    "I looked that up online, so I won't press anything for it. Say the command again."
)
NO_PRESS_AFTER_RECALL = (
    "That's from an earlier talk, so I won't press it again. Ask for it now if you want it."
)
HANDED_OVER = "On it."
"""What ``plot_route`` tells the brain to reply (#647): Tycho plots the route and says
how it went, so these words are never spoken."""
ACT_HELD = "On it: just the route."
"""The reply instead of an act after the brain handed a route over (#721). Its own words,
so the route and the trace know an act was held (#728). Never spoken either."""
ROUTE_DROPPED = "I don't plot a route after a look or a look-up. Ask for the route on its own."
"""Said instead of ``HANDED_OVER`` when the route was dropped (#728): nothing was done."""
CUT_SHORT = "That answer ran too long for me. Ask me for part of it."
"""Said when a reply hit the output limit before it was whole (#266): true whatever was cut."""
STOPPED = {
    NO_PRESS_AFTER_SEARCH: "act after a web search; not pressed",
    NO_PRESS_AFTER_RECALL: "act after a search of past conversations; not pressed",
    ACT_HELD: "act after a route hand-over; not pressed",  # #728
    CUT_SHORT: "cut off at the output limit",
}
"""Why a reply stopped, for its trace."""


def hands_over(lookup: object, arguments: object) -> bool:
    """Whether ``lookup`` takes the call to plot a route itself (``RouteTool.hands_over``,
    #721, #728): then nothing presses after it in the same request."""
    hands = getattr(lookup, "hands_over", None)
    return callable(hands) and bool(hands(arguments))


RECALLED = "searched past conversations (its words are left out here: never pressed again)"
"""A search of the history in the follow-ups (ADR 14): what it found stays out of them."""
LOOKED_UP_CHARS = 80
"""The most of a look-up's words the follow-ups keep."""


def looked_up(name: str, arguments: object, *, web: bool) -> str:
    """A look-up in a few words, for the follow-ups (#564): a web search by its query,
    any other by its name and what it was asked."""
    asked: Mapping[str, object] = {}
    if isinstance(arguments, dict):
        asked = cast(Mapping[str, object], arguments)
    if web:
        query = str(asked.get("query", "")).strip()
        return f'searched the web for "{query[:LOOKED_UP_CHARS]}"'
    values: list[str] = []
    for value in asked.values():  # ship_lookup and item_lookup take lists of names
        items = cast(list[object], value) if isinstance(value, list) else [value]
        values += [str(v).strip() for v in items if isinstance(v, str | int | float)]
    words = ", ".join(v for v in values if v)
    return f"looked up {name}" + (f" ({words[:LOOKED_UP_CHARS]})" if words else "")
