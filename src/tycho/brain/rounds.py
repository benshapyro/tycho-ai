"""What every brain adapter does with a tool call, whatever model it talks to (ADR 14).

A request is up to ``MAX_CALLS`` calls to the model. Each call is a *round*: as each
tool call in the reply is complete, ``Round.tool_call`` starts its look-up, refuses an
act that names unbound actions, or returns the act, which ends the request. The
adapters (``claude``, ``openrouter``) only read their own provider's stream and say
when a tool call is complete, so the rules live here once:

- only the first act in a reply is pressed;
- a fleet or settings note (``instant``) in the same reply as an act finishes first,
  for up to ``INSTANT_SECONDS``;
- nothing presses after a web search (``NO_PRESS_AFTER_SEARCH``);
- "Checking." is said only once a reply with look-ups is known not to end in a press.

When tracing is on (``traces``, #282), the round records each call, tool call and
look-up on the turn's trace, and ``traced`` writes the request's line once it ends.
"""

import asyncio
import dataclasses
import difflib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from tycho.brain.decisions import BrainError, Decision, Reply, UnboundAction, parse_tool_call
from tycho.brain.lookups import LOOKUP_SECONDS, Lookup, LookupResult, failure, run_lookup
from tycho.brain.traces import Look, Trace, TraceFile
from tycho.text import clip_spoken

log = logging.getLogger(__name__)

MAX_CALLS = 3
"""Calls to the model per request: up to two rounds of look-ups, then the answer (ADR 15)."""
NO_PRESS_AFTER_SEARCH = (
    "I looked that up online, so I won't press anything for it. Say the command again."
)
ACTS = frozenset({"run_actions", "run_sequence", "show_card"})
INSTANT_SECONDS = 1.0
"""How long an act waits for a fleet or settings note from the same reply."""

SECOND_ACT = (
    "Not done: an earlier action in this reply was refused, so {tool} wasn't used. Call "
    "it again on its own if it's still wanted."
)
LAST_PICK = "No more look-ups for this request: pick the actions again, or say what you can't do."
"""Instead of LAST_CALL, when the turn holds a refused act (review of #177)."""

LAST_CALL = (
    "No more look-ups for this request. Answer now with what you found: speak, or "
    "use show_card. If something wasn't found, say so briefly."
)
"""Added to the results before the last call, so a third look-up isn't the end."""
CUT_SHORT = "That answer ran too long for me. Ask me for part of it."
"""Said when the model's reply hit the output limit before it was whole: a how-to or a
plan on a card can (#266). Better than the error sound, and true whichever part was cut."""
CUT_OFF = frozenset({"max_tokens", "length"})
"""How each provider says a reply stopped at the output limit (Anthropic, OpenAI's shape)."""
TOOL_AS_TEXT = re.compile(r"\b[a-z_]{3,}\s*\(\s*\{", re.IGNORECASE)
"""A tool call written as text: never said out loud."""
_MARKUP = re.compile(r"[*`#]+")


def tool_specs(
    sequences: list[str], lookups: Iterable[Lookup] = (), *, keys: bool = True
) -> list[dict[str, object]]:
    """Every tool but ``show_card``, as name, description and JSON schema: each adapter
    puts them in its own provider's shape. Without ``keys`` (advisor mode, #191) there's
    no ``run_actions`` or ``run_sequence``: only look-ups."""
    looks: list[dict[str, object]] = [
        {
            "name": lookup.name,
            "description": lookup.description,
            "input_schema": dict(lookup.input_schema),
        }
        for lookup in lookups
    ]
    if not keys:
        return looks
    return [
        {
            "name": "run_sequence",
            "description": "Run one of the player's named sequences.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sequences or [""]},
                    "say": {"type": "string", "description": "A short spoken line."},
                },
                "required": ["name", "say"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_actions",
            "description": "Press game actions, in order, with the player's own keys.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Action names from the list, e.g. v_flightready.",
                    },
                    "say": {"type": "string", "description": "A short spoken line."},
                },
                "required": ["actions", "say"],
                "additionalProperties": False,
            },
        },
        *looks,
    ]


@dataclass
class Turn:
    """What one request has done so far, across its calls to the model."""

    waiting: Callable[[], None] | None = None
    looking: Callable[[], None] | None = None
    checking: Callable[[], None] | None = None
    searched: bool = False
    credits: list[str] = field(default_factory=list[str])
    """Sources an answer must name, from the look-ups whose results it got ("UEX", #164)."""
    sources: dict[str, str] = field(default_factory=dict[str, str])
    """Each look-up's source, by its tool call's id, until its result is in."""
    unbound: UnboundAction | None = None
    """The last act refused for naming unbound actions, for the final error (#167)."""
    refused: bool = False
    """This call's reply held a refused act: its result asks for another pick."""
    trace: Trace | None = None
    """What the request did, when tracing is on (#282)."""


class Round:
    """One call to the model: its tool calls, handled as each one is complete.

    ``looking`` holds every tool call that needs a result sent back (look-ups, and
    refused acts with why), by its id, in the order the model made them."""

    def __init__(
        self,
        turn: Turn,
        lookups: Mapping[str, Lookup],
        actions: Collection[str],
        sequences: Collection[str],
        started: float,
    ) -> None:
        self.turn = turn
        self.looking: list[tuple[str, asyncio.Task[LookupResult]]] = []
        self._lookups = lookups
        self._actions = actions
        self._sequences = sequences
        self._started = started
        self._checking = False
        self._instant: list[asyncio.Task[LookupResult]] = []
        """A fleet or settings note in the same reply as an act: it runs too (#156)."""
        self._looks: dict[asyncio.Task[LookupResult], Look] = {}
        """Each traced look-up's line entry, by its task."""
        turn.refused = False
        if turn.trace is not None:
            turn.trace.call()

    def streaming(self) -> None:
        """A piece of this call's stream arrived: the first one is the time to first
        token in the trace."""
        if self.turn.trace is not None:
            self.turn.trace.streaming()

    def used(
        self,
        tokens: Mapping[str, int],
        cost: float | None,
        stop: str,
        host: str = "",
        priced: str = "",
    ) -> None:
        """This call ended with a whole reply: its tokens, cost (and where the cost came
        from) and why it stopped."""
        if self.turn.trace is not None:
            self.turn.trace.used(tokens, cost, stop, host, priced)

    def left(self, output_chars: int, tokens: Mapping[str, int] | None = None) -> None:
        """The caller left this call's stream at an act: how much the model had written
        by then, and the tokens known so far, for the trace (#387)."""
        if self.turn.trace is not None:
            self.turn.trace.left(output_chars, tokens)

    async def tool_call(self, use_id: str, name: str, arguments: object) -> Decision | None:
        """A complete tool call. Returns the act to take, which ends the request (the
        caller leaves the stream there, so only the first act in a reply is pressed),
        or None to read on. Raises ``BrainError`` for an act that can't be used."""
        turn = self.turn
        log.debug("Brain chose %s in %.0f ms", name, ms(self._started))
        if turn.trace is not None:
            turn.trace.tool(name, arguments)
        if name in self._lookups:
            # Start now; the model's message may still be finishing.
            chosen = self._lookups[name]
            if getattr(chosen, "from_the_web", False):
                log.debug("Brain searching the web at %.0f ms", ms(self._started))
                turn.searched = True
                if turn.waiting is not None:
                    turn.waiting()
                    turn.waiting = None  # once a request
                turn.looking = turn.checking = None  # not after a search's line
            elif turn.looking is not None and not getattr(chosen, "instant", False):
                # The deadline moves now: the rest of the reply can take longer than
                # what's left of it (#178). "Checking." waits until the reply is known
                # not to end in an act (#176).
                turn.looking()
                self._checking = True
            seconds = float(getattr(chosen, "seconds", LOOKUP_SECONDS))
            lookup = run_lookup(chosen, arguments, seconds)
            turn.sources[use_id] = str(getattr(chosen, "credit", ""))
            task = asyncio.create_task(lookup)
            if turn.trace is not None:
                self._looks[task] = _trace_lookup(turn.trace, name, task)
            self.looking.append((use_id, task))
            if getattr(chosen, "instant", False):
                self._instant.append(task)
            return None
        if turn.refused:
            # Only the first act in a reply may be pressed, even when that one was
            # refused (#176): this one waits for the next call.
            self.looking.append((use_id, answered(failure(SECOND_ACT.format(tool=name)))))
            return None
        try:
            decided = parse_tool_call(name, arguments, self._actions, self._sequences)
        except UnboundAction as e:
            # Not pressed: the model hears why, with the closest bound names, and
            # picks again on the next call (#164).
            log.debug("Brain named unbound actions: %s", e.unknown)
            turn.unbound, turn.refused = e, True
            self.looking.append((use_id, answered(unbound(e, self._actions))))
            return None
        await self._end()
        return decided

    async def cut_short(self, name: str) -> Reply:
        """A tool call the output limit cut off (#266): its arguments aren't all there,
        so nothing of it is used. It ends the request with ``CUT_SHORT``."""
        log.warning("The brain's %s was cut off at the output limit; not used", name)
        await self._end()
        return Reply(CUT_SHORT)

    async def _end(self) -> None:
        """The request ends in this reply. Ending it here means only the first act in a
        reply is ever pressed. A look-up started with it is no longer needed; a fleet or
        settings note is ("I'm in my Pitbull, power off", #156): it's local and quick, so
        it finishes first."""
        for _, task in self.looking:
            if task not in self._instant:
                self._stop(task)
        if self._instant:
            _, pending = await asyncio.wait(self._instant, timeout=INSTANT_SECONDS)
            if pending:
                log.warning(
                    "Pressing before %d fleet or settings note(s) finished; they carry on",
                    len(pending),
                )

    def finished(self) -> None:
        """The reply ended without an act. Not beside a refused act, the next call may
        press (#214), so "Checking." is said now if a look-up started. Its line isn't
        owed later either: by the next reply's end, the results are in."""
        turn = self.turn
        if self._checking and not turn.refused and turn.checking is not None:
            turn.checking()
            turn.checking = None  # once a request

    def cancel(self) -> None:
        """The call failed or was cancelled: stop every look-up it started."""
        for _, task in self.looking:
            self._stop(task)

    def _stop(self, task: asyncio.Task[LookupResult]) -> None:
        """Stop a look-up, and say so in its trace now: the task's done callback only
        runs after the line has been written (#305)."""
        if task.cancel() and (look := self._looks.get(task)) is not None:
            look.cancelled = True

    async def results(self) -> list[LookupResult]:
        """Every look-up's result, in the order the model asked, noting each source
        whose data the answer used (not a miss, an error, or a look-up a card cut
        short: review of #179)."""
        results = list(await asyncio.gather(*(task for _, task in self.looking)))
        for (use_id, _), result in zip(self.looking, results, strict=True):
            credit = self.turn.sources.get(use_id, "")
            if credit and found(result) and credit not in self.turn.credits:
                self.turn.credits.append(credit)
        return results


async def traced(traces: TraceFile | None, turn: Turn, deciding: Awaitable[Decision]) -> Decision:
    """The request's decision, with its trace line written once it ends, however it
    ends (#282). Without ``traces`` (players, unless ``TYCHO_TRACE`` is on), only the
    decision."""
    trace = turn.trace
    if traces is None or trace is None:
        return await deciding
    try:
        decided = await deciding
    except BaseException as e:
        trace.failed(e)
        traces.write(trace)
        raise
    stopped = ""
    if isinstance(decided, Reply) and decided.text == NO_PRESS_AFTER_SEARCH:
        stopped = "act after a web search; not pressed"
    elif isinstance(decided, Reply) and decided.text == CUT_SHORT:
        stopped = "cut off at the output limit"
    trace.ended(decided, stopped)
    traces.write(trace)
    return decided


def _trace_lookup(trace: Trace, name: str, task: asyncio.Task[LookupResult]) -> Look:
    """Note the look-up's time and result size in the trace when it's done."""
    started = time.perf_counter()
    look = Look(name, trace.elapsed())
    trace.looked(look)  # now, so one still running when the request ends is there too

    def done(finished: asyncio.Task[LookupResult]) -> None:
        look.ms = trace.elapsed()
        look.took_ms = round((time.perf_counter() - started) * 1000, 1)
        if finished.cancelled():
            look.cancelled = True
        elif (error := finished.exception()) is not None:
            look.error = True
            log.debug("Look-up %s raised %s", name, type(error).__name__)
        else:
            result = finished.result()
            look.chars, look.error, look.found = len(result.content), result.is_error, found(result)

    task.add_done_callback(done)
    return look


def last_note(call: int, turn: Turn) -> str | None:
    """What to add to the results before the last call: answer with what's here, a
    card included; beside a refused act, pick again or answer, but look nothing up."""
    if call != MAX_CALLS - 2:
        return None
    return LAST_PICK if turn.refused else LAST_CALL


def answered(result: LookupResult) -> asyncio.Task[LookupResult]:
    """A result that's ready now, in the shape of a look-up still running."""

    async def ready() -> LookupResult:
        return result

    return asyncio.create_task(ready())


def unbound(error: UnboundAction, actions: Collection[str]) -> LookupResult:
    """What the model hears when it names actions that aren't bound: the closest that
    are, or that there's no key for it, so it says so."""
    close = {
        name: difflib.get_close_matches(name, actions, n=3, cutoff=0.5) for name in error.unknown
    }
    hints = "; ".join(
        f"{name}: did you mean {', '.join(found)}?" if found else f"{name}: nothing close is bound"
        for name, found in close.items()
    )
    return failure(
        f"Nothing was pressed. Not bound: {hints}. Pick from the bound actions, or say "
        "there's no key for it and what to bind."
    )


def found(result: LookupResult) -> bool:
    """Whether a look-up's result holds data: not an error, not a miss."""
    return not result.is_error and '"not_found"' not in result.content


def credited(reply: Reply, credits: Sequence[str]) -> Reply:
    """The answer, naming each source it used that it didn't name itself: the model is
    asked to credit UEX, but with the details on a card it often doesn't (#164)."""
    said = re.sub(r"\b(\w)\.(?=\w\.)", r"\1", reply.text)  # "U.E.X." names UEX too
    missing = [c for c in credits if not re.search(rf"\b{re.escape(c)}", said, re.IGNORECASE)]
    if not missing:
        return reply
    return dataclasses.replace(reply, text=f"{reply.text} From {' and '.join(missing)}.")


def final_reply(text: str, credits: Sequence[str], stopped: str) -> Reply:
    """A reply in words, as one line to say. Raises ``BrainError`` if there's nothing
    to say. A tool call written as text ('show_card({"say": ...') is gibberish read
    out: what came before it can be said; with nothing before, it fails."""
    reply = spoken(text)
    if not reply and stopped in CUT_OFF:
        log.warning("The brain's reply stopped at the output limit with nothing to say")
        return Reply(CUT_SHORT)
    if not reply:
        raise BrainError(f"no answer (stopped: {stopped})")
    if found_call := TOOL_AS_TEXT.search(reply):
        reply = reply[: found_call.start()].strip()
        if not reply:
            raise BrainError("it wrote a tool call as text")
    return fitted(clip_spoken(reply, SPOKEN_LIMIT), credits)


SPOKEN_LIMIT = 400
"""The most a spoken reply may be, credits and all: the voice's own cap (#350)."""


def fitted(reply: str, credits: Sequence[str]) -> Reply:
    """``reply`` with its credits, cut at a sentence end to leave them room, so the
    voice's cap never takes them, or half a sentence, off (#350)."""
    answer = credited(Reply(reply), credits)
    if len(answer.text) <= SPOKEN_LIMIT:
        return answer
    room = SPOKEN_LIMIT - (len(answer.text) - len(reply))
    return credited(Reply(clip_spoken(reply, room)), credits)


def spoken(text: str) -> str:
    """A reply as one line to say: no markdown (it would be read out), no line breaks."""
    return " ".join(_MARKUP.sub("", text).split())


def ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
