"""What every brain adapter does with a tool call, whatever model it talks to (ADR 14).

A request is up to ``MAX_CALLS`` calls to the model. Each call is a *round*: as each
tool call in the reply is complete, ``Round.tool_call`` starts its look-up, refuses an
act that names unbound actions, or returns the act, which ends the request. Adapters
only read their own provider's stream and say when a tool call is complete, so:

- only the first act in a reply is pressed;
- a fleet or settings note (``instant``) in the same reply as an act finishes first,
  for up to ``INSTANT_SECONDS``;
- nothing presses after a web search, a search of past talk or a route (``unpressed``);
- words that don't ask for the act are answered, never pressed (``unasked``, #708);
- "Checking." is said only once a reply with look-ups is known not to end in a press.

When tracing is on (``traces``, #282), the round records each call, tool call and
look-up on the turn's trace, and ``traced`` writes the request's line once it ends.
"""

import asyncio
import dataclasses
import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field

from tycho.brain.decisions import (
    BrainError,
    BrainRequest,
    Decision,
    EmptyAct,
    NotAnAct,
    Reply,
    RunActions,
    RunSequence,
    UnboundAction,
    parse_tool_call,
)
from tycho.brain.heard import Heard, hearing
from tycho.brain.lookups import (
    CUT_SHORT,
    HANDED_OVER,
    LOOKUP_SECONDS,
    NO_PRESS_AFTER_RECALL,
    NO_PRESS_AFTER_SEARCH,
    RECALLED,
    STOPPED,
    Lookup,
    LookupResult,
    failure,
    hands_over,
    looked_up,
    run_lookup,
)
from tycho.brain.refusals import unbound
from tycho.brain.traces import Look, Trace, TraceFile
from tycho.brain.unasked import unasked
from tycho.text import clip_spoken

log = logging.getLogger(__name__)

MAX_CALLS = 3
"""Calls to the model per request: up to two rounds of look-ups, then the answer (ADR 15)."""
ACTS = frozenset({"run_actions", "run_sequence", "show_card"})
INSTANT_SECONDS = 1.0
"""How long an act waits for a fleet or settings note from the same reply."""

SECOND_ACT = (
    "Not done: an earlier action in this reply was refused, so {tool} wasn't used. Call "
    "it again on its own if it's still wanted."
)
EMPTY_ACT = EmptyAct.answer
"""The result for an empty act with nothing real to say (#666): it isn't an answer."""
LAST_PICK = "No more look-ups for this request: pick the actions again, or say what you can't do."
"""Instead of LAST_CALL, when the turn holds a refused act (review of #177)."""

LAST_CALL = (
    "No more look-ups for this request. Answer now with what you found: speak, or "
    "use show_card. If something wasn't found, say so briefly."
)
"""Added to the results before the last call, so a third look-up isn't the end."""
CARD_NOTE = (
    "If your answer needs more than about three items or numbers (prices, a route, "
    "specs, steps), answer with show_card: one sentence to say, the rest on the card. "
    "An answer that fits in one short sentence (who makes it, one pick) is only said."
)
"""Added to a look-up's results while the Tile is on (#490). The briefing's rule is far
from the answer: in play, a trade route and a loadout were spoken, numbers and all."""
CUT_OFF = frozenset({"max_tokens", "length"})
"""How each provider says a reply stopped at the output limit (Anthropic, OpenAI's shape)."""
TOOL_AS_TEXT = re.compile(r"\b[a-z_]{3,}\s*\(\s*\{", re.IGNORECASE)
"""A tool call written as text: never said out loud."""
_MARKUP = re.compile(r"[*`#]+")


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
    asked: bool = False  # the last refusal was a question's: answer in words (#714)
    looked: bool = False
    """This reply looked something up to answer (no note): its results get ``CARD_NOTE`` (#490)."""
    trace: Trace | None = None
    """What the request did, when tracing is on (#282)."""
    picture: int = 0
    """The size of the game window's picture sent with the request (#31): all that's traced."""
    looked_up: list[str] = field(default_factory=list[str])
    """What the request looked up, in a few words each, for the follow-ups (#564)."""
    answering: Callable[[], None] | None = None
    """Called as each call's stream starts: the cache was read, even if it's cut off (#540)."""
    heard: Heard | None = None  # what the player said, for the memory tool (ADR 8)
    recalled: bool = False  # searched past conversations: nothing presses (ADR 14, #484)
    handed: bool = False  # a place went to the route tool (``hands_over``): nothing presses

    @classmethod
    def of(cls, request: BrainRequest, answering: Callable[[], None] | None = None) -> Turn:
        """A new turn for ``request``, with its callbacks and the adapter's ``answering``."""
        size = request.picture.size if request.picture is not None else 0
        turn = cls(
            request.waiting, request.looking, request.checking, picture=size, answering=answering
        )
        turn.heard = Heard.of(request)
        return turn


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
        self._streamed = False
        self._empty = 0
        """Empty acts in this reply (#666): answered with ``EMPTY_ACT``, never said."""
        self._instant: list[asyncio.Task[LookupResult]] = []
        """A fleet or settings note in the same reply as an act: it runs too (#156)."""
        self._looks: dict[asyncio.Task[LookupResult], Look] = {}
        """Each traced look-up's line entry, by its task."""
        turn.refused = turn.looked = False
        if turn.trace is not None:
            turn.trace.call()

    def streaming(self, *, answering: bool = True) -> None:
        """A piece of this call's stream arrived: the first one is the time to first
        token in the trace. The first that is part of an answer (``answering``: not an
        error OpenRouter sent inside the stream, #576) means the cache was read."""
        if answering and not self._streamed:
            self._streamed = True
            if self.turn.answering is not None:
                self.turn.answering()
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
            turn.handed |= hands_over(chosen, arguments)  # #721, #728
            if getattr(chosen, "recalls", False):
                turn.recalled = True
                turn.looked_up.append(RECALLED)  # its words stay out of the follow-ups
            web = bool(getattr(chosen, "from_the_web", False))
            if web or not getattr(chosen, "instant", False):  # not a fleet or settings note
                turn.looked_up.append(looked_up(name, arguments, web=web))
            seconds = float(getattr(chosen, "seconds", LOOKUP_SECONDS))
            lookup = run_lookup(chosen, arguments, seconds)
            turn.sources[use_id] = str(getattr(chosen, "credit", ""))
            task = asyncio.create_task(lookup)
            if turn.trace is not None:
                self._looks[task] = _trace_lookup(turn.trace, name, task)
            self.looking.append((use_id, task))
            if getattr(chosen, "instant", False):
                self._instant.append(task)
            else:
                turn.looked = True
            return None
        if turn.refused:
            # Only the first act in a reply may be pressed, even when that one was
            # refused (#176): this one waits for the next call.
            self.looking.append((use_id, answered(failure(SECOND_ACT.format(tool=name)))))
            return None
        said = turn.heard.said if turn.heard is not None else ""
        try:
            decided = parse_tool_call(name, arguments, self._actions, self._sequences, said)
        except NotAnAct as e:
            # Not an answer ("unused", #666; "how do I...?"): the model hears so and
            # answers next call, unless this reply's words already did (``said``).
            log.debug("Brain's act wasn't one: %s", e)
            self._empty += isinstance(e, EmptyAct)
            turn.asked |= e.question
            self.looking.append((use_id, answered(failure(e.answer))))
            return None
        except UnboundAction as e:
            # Not pressed: the model hears why, with the closest bound names, and
            # picks again on the next call (#164).
            log.debug("Brain named unbound actions: %s", e.unknown)
            turn.unbound, turn.refused, turn.asked = e, True, False
            self.looking.append((use_id, answered(unbound(e, self._actions))))
            return None
        if (n := _named(decided)) and (why := unasked(turn.heard, n, self._actions)):
            log.debug("Brain chose to act on words that didn't ask for it; not pressing")
            turn.refused = turn.asked = True
            self.looking.append((use_id, answered(failure(why))))
            return None
        await self._end()
        return decided

    def said(self, text: str, credits: Sequence[str], stopped: str) -> Reply | None:
        """The reply's words as the answer, if they end the request: it looked nothing
        up (``final_reply``, which raises if there's nothing to say), or its only tool
        calls were empty acts after words to say (#666). None if results are owed."""
        if not self.looking:
            return final_reply(text, credits, stopped)
        if not self._empty or self._empty != len(self.looking) or not spoken(text):
            return None
        if _LEAD_IN.match(spoken(text)):
            return None  # "Let me check your blueprints." isn't the answer (#669)
        try:
            return final_reply(text, credits, stopped)
        except BrainError:
            return None

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
    """The request's decision, its trace line written however it ends (#282; only with
    ``traces``), and what it looked up (#564). Its look-ups see what was ``heard``."""
    with hearing(turn.heard):
        return await _traced(traces, turn, deciding)


async def _traced(traces: TraceFile | None, turn: Turn, deciding: Awaitable[Decision]):
    trace = turn.trace
    if traces is None or trace is None:
        return noted(await deciding, turn)
    trace.picture = turn.picture
    try:
        decided = noted(await deciding, turn)
    except BaseException as e:
        trace.failed(e)
        traces.write(trace)
        raise
    stopped = STOPPED.get(decided.text, "") if isinstance(decided, Reply) else ""
    trace.ended(decided, stopped)
    traces.write(trace)
    return decided


def unpressed(turn: Turn, decided: Decision) -> Reply | None:
    """Said instead of an act after a web search, a search of past talk or a route (#721)."""
    if isinstance(decided, Reply) or not (turn.searched or turn.recalled or turn.handed):
        return None
    log.warning("The brain chose to act after a search or a route; not pressing")
    stopped = NO_PRESS_AFTER_SEARCH if turn.searched else NO_PRESS_AFTER_RECALL
    return Reply(stopped if turn.searched or turn.recalled else HANDED_OVER)


def noted(decided: Decision, turn: Turn) -> Decision:
    """A reply with what its request looked up, for the follow-ups (#564)."""
    if isinstance(decided, Reply) and turn.looked_up:
        return dataclasses.replace(decided, looked_up=tuple(turn.looked_up))
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


def last_note(call: int, turn: Turn, cards: bool = False) -> str | None:
    """What to add to a call's results: with the Tile on, after a look-up, when to use a
    card (#490); before the last call, answer with what's here, a card included; beside
    a refused act, pick again or answer, but look nothing up."""
    notes: list[str] = []
    if cards and turn.looked and not turn.refused:
        notes.append(CARD_NOTE)
    if call == MAX_CALLS - 2:
        notes.append(LAST_PICK if turn.refused and not turn.asked else LAST_CALL)
    return " ".join(notes) or None


def answered(result: LookupResult) -> asyncio.Task[LookupResult]:
    """A result that's ready now, in the shape of a look-up still running."""

    async def ready() -> LookupResult:
        return result

    return asyncio.create_task(ready())


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


_LEAD_IN = re.compile(
    r"(let me|i['\u2019]ll (check|look|see|find|pull)|i will (check|look)|checking|one (moment|sec)"
    r"|just a (moment|sec)|hold on|give me a)\b[^.!?…,]{0,40}[.!?…]*$",
    re.IGNORECASE,
)
"""Words that only say an answer is coming, in one short sentence with no comma: never
the answer (#669). "Looking at your fleet, none can mine." is an answer."""


def spoken(text: str) -> str:
    """A reply as one line to say: no markdown (it would be read out), no line breaks."""
    return " ".join(_MARKUP.sub("", text).split())


def _named(decided: Decision) -> tuple[str, ...]:
    """The action or sequence names an act would press; none for a reply or a card."""
    if isinstance(decided, RunActions):
        return decided.actions
    return (decided.name,) if isinstance(decided, RunSequence) else ()


def ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
