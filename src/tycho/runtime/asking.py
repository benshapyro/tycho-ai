"""Asking the brain, and the confirm question (ADR 13, ADR 14). Out of ``session.py``
(#399): the session still decides when to ask, and keeps the question that's open.

The brain gets a time to answer in, which look-ups and web searches move on, up to a
limit (#150, #169, #314). A confirm-grade request waits for the player's "confirm";
"cancel" and "say that again" answer it too (#226, #351).
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace

from tycho.brain import BrainError, BrainRequest, Exchange, Reply
from tycho.commands import Command, CommandMatcher
from tycho.eyes import Picture
from tycho.keybinds import Keybinds
from tycho.packs import Safety
from tycho.runtime.conversation import Line
from tycho.runtime.messages import Progress, Result, Status, Trace
from tycho.runtime.ports import Brain
from tycho.runtime.routing import Picked, from_brain
from tycho.runtime.speaking import Speaking

CONFIRM_SECONDS = 5.0
"""How long a question waits for "confirm", unless the settings say otherwise."""
BRAIN_SECONDS = 8.0
"""How long to wait for the brain before saying it couldn't answer (ADR 14)."""
SEARCH_SECONDS = 12.0
"""Once a web search starts, the brain has this long from then (searches take 4-8 s)."""
LOOKING_SECONDS = 7.0
"""As each other look-up starts ("Checking." is said for the first), the brain has at
least this long from then: a look-up and Claude's answer to it take 3-5 s, and two in
a row ran past BRAIN_SECONDS in the play session (#150). Never less than
BRAIN_SECONDS, and never past LOOKING_LIMIT."""
SEARCH_LIMIT = 20.0
"""A request with a web search answers or fails this long after it was asked."""
OPEN_SEARCH_SECONDS = 27.0
OPEN_SEARCH_LIMIT = 32.0
"""With ``web_search_open`` (#354, on by default): two searches and page reads take longer,
up to the search's own 25 s, so the brain gets more time once it starts."""
LOOKING_LIMIT = 15.0
"""However many look-ups, an answer comes or fails this long after the request."""


@dataclass(frozen=True, slots=True)
class Times:
    """How long the brain has: at first, once a search starts, once a look-up starts,
    and the limits on both."""

    brain: float = BRAIN_SECONDS
    search: float = SEARCH_SECONDS
    search_limit: float = SEARCH_LIMIT
    looking: float = LOOKING_SECONDS
    looking_limit: float = LOOKING_LIMIT


@dataclass(frozen=True, slots=True)
class Question:
    """A confirm-grade request waiting for the player's "confirm"."""

    request: int
    commands: tuple[Command, ...]
    deadline: float
    by_brain: bool = False
    say: str = ""


async def ask_brain(
    brain: Brain,
    text: str,
    keybinds: Keybinds,
    follow_ups: tuple[Exchange, ...],
    own_keys: str,
    *,
    times: Times,
    matcher: CommandMatcher,
    lists: Safety,
    searching: Callable[[], None],
    checking: Callable[[], None],
    picture: Picture | None = None,
    may_look: bool = False,
) -> Picked | Reply | str:
    """What the brain decides, within its time (ADR 14, #150, #169): commands, a
    reply, or why it couldn't answer. ``own_keys`` names the player's keys for a
    built-in command said with key actions off (#386). ``searching`` and ``checking``
    hear a web search or another look-up start, to say a line while it runs.
    ``picture``: the game window, for a look (#31); ``may_look``: looking is on, so
    the brain may offer one."""
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(times.brain) as budget:
            asked = loop.time()

            def search_started() -> None:
                # Capped (#169): after look-ups, a search can't run past the limit.
                # Never earlier than brain_seconds, though (#314): a long one stands.
                when = budget.when()
                later = loop.time() + times.search
                cap = asked + max(times.search_limit, times.brain)
                budget.reschedule(min(max(later, when or later), cap))
                searching()

            def looking() -> None:
                # Only ever later, and never past the limit: an early look-up
                # keeps the usual deadline, and each one after moves it on.
                later = min(loop.time() + times.looking, asked + times.looking_limit)
                if (when := budget.when()) is None or later > when:
                    budget.reschedule(later)

            decision = await brain.decide(
                BrainRequest(
                    text,
                    keybinds,
                    follow_ups,
                    own_keys,
                    waiting=search_started,
                    looking=looking,
                    checking=checking,
                    picture=picture,
                    may_look=may_look,
                )
            )
        return from_brain(decision, matcher, lists)
    except (BrainError, TimeoutError) as e:
        return str(e) or "it took too long"


@dataclass(frozen=True, slots=True)
class Asker:
    """The session's way to the brain: ``ask_brain`` with what it needs each time. The
    voice warms up while the brain thinks, so speech starts sooner."""

    brain: Brain | None
    """None: no brain, so nothing is routed here."""
    times: Times
    matcher: CommandMatcher
    lists: Safety
    voice: Speaking
    keybinds: Callable[[], Keybinds]
    follow_ups: Callable[[], tuple[Exchange, ...]]
    may_look: Callable[[], bool]
    """Looking at the game window is on, so the brain may offer a look (#31)."""

    async def ask(
        self, text: str, own_keys: str = "", picture: Picture | None = None
    ) -> Picked | Reply | str:
        """What the brain decides, with the game window's picture for a look (#31),
        which is dropped once the request ends."""
        assert self.brain is not None  # noqa: S101 - only routed here with a brain
        self.voice.warm()
        return await ask_brain(
            self.brain,
            text,
            self.keybinds(),
            self.follow_ups(),
            own_keys,
            times=self.times,
            matcher=self.matcher,
            lists=self.lists,
            searching=self.voice.fill,
            checking=self.voice.check,
            picture=picture,
            may_look=self.may_look(),
        )


@dataclass(frozen=True, slots=True)
class Answer:
    """How a word that answers the question ends the request: nothing is pressed."""

    status: Status
    detail: str = ""
    commands: tuple[str, ...] = ()
    say: str = ""
    question: str = ""
    """The question, asked again."""
    reopened: Question | None = None
    """The question asked again, waiting afresh for "confirm"."""

    def result(self, request: int, trace: Trace, heard: str, text: str) -> Result:
        """The request this answer ends."""
        progress = Progress(self.commands, say=self.say, question=self.question)
        return progress.result(request, self.status, trace, heard, text, self.detail)


def answered(word: str, question: Question | None, now: float) -> Answer | Question:
    """ "Confirm" or "cancel": the question to go ahead with, once confirmed, or how the
    request ends. A question lasts until the player next says something (ADR 13,
    amended for #226), so an expired one is answered by neither."""
    if question is None or now > question.deadline:
        detail = f"nothing to {word}" if question is None else "the question expired"
        if word == "cancel":
            # "Never mind" with nothing to cancel is still heard (#351): no not-done
            # sound, and the talk so far is let go.
            return Answer("done", detail, commands=("cancel",))
        return Answer("not_understood", detail)
    if word == "cancel":
        asked = "+".join(command.id for command in question.commands)
        # "cancel" is never logged as the cancelled command
        return Answer("done", f"cancelled #{question.request} ({asked})", commands=("cancel",))
    return question


def say_again(line: Line, question: Question | None, now: float, confirm_seconds: float) -> Answer:
    """The last line Tycho said, word for word, with no call to the brain (#222): an
    answer, or the line about what it did (#226). Only ever spoken: nothing is planned
    or pressed, and a card isn't redrawn (that's "show that again").

    A confirm question still open is asked again, and waits afresh for "confirm"
    (ADR 13, amended for #226). One that expired or was answered isn't: "Say confirm."
    would then ask for something that does nothing."""
    tag = ("say_again",)
    if line.question is not None:
        if question is None or question.request != line.question:
            return Answer("not_understood", "the question has ended", tag)
        if now > question.deadline:
            return Answer("not_understood", "the question expired", tag)
        reopened = replace(question, deadline=now + confirm_seconds)
        detail = 'asked again: hold push-to-talk and say "confirm"'
        return Answer("asking", detail, tag, question=line.text, reopened=reopened)
    said = line.text.strip()
    if not said:
        return Answer("not_understood", "nothing to say again", tag, say=said)
    return Answer("done", "", tag, say=said)
