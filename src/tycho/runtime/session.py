"""The talking loop: hold push-to-talk, speak, and Tycho presses the right keys (ADR 11).

One asyncio loop coordinates; nothing blocking runs on it:
- push-to-talk is polled on its own thread and handed over with call_soon_threadsafe;
- transcription has one worker thread;
- key presses have one worker thread, so only one press happens at a time.

One request at a time. Pressing push-to-talk again cancels whatever is in
flight, and a key sequence stops within about 10 ms with every key released.

Confirm-grade requests are asked about (ADR 13): a question sound, then nothing
is pressed unless the next thing the player says is "confirm", within a few
seconds; "say that again" asks the question again and restarts the wait. Expiry
is checked when they next speak; no timer acts on its own.

What the fast path can't match goes to the brain (ADR 14), with the last few
exchanges if they were recent. What it picks becomes commands, planned and gated
exactly like the fast path's.

Who speaks when (ADR 14): the fast path keeps its sounds. The brain's replies
are spoken, and so is its line about what it did, unless the game announces
that itself. Pressing push-to-talk stops Tycho talking at once.

The session keeps the threads, cancellation and barge-in, and the confirm question.
Where words go is ``routing``, what gets pressed is ``planning`` (both pure, so the
brain eval grades the same code), the log is the ``Reporter``, and what was said
lately is the ``Conversation`` (#55).
"""

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from tycho.audio.clip import Samples, trim_silence
from tycho.audio.source import AudioError, AudioSource
from tycho.brain import BrainError, BrainRequest, Reply
from tycho.commands import Command, CommandMatcher
from tycho.commands.reserved import (
    CANCEL_WORDS,
    CONFIRM_WORDS,
    SAY_AGAIN_WORDS,
    TILE_WORDS,
)
from tycho.game.keybinds import Keybinds
from tycho.input import Presser
from tycho.lexicon import Lexicon
from tycho.packs import Safety
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime.conversation import (
    FOLLOW_UP_LIMIT,
    FOLLOW_UP_SECONDS,
    Conversation,
    would_speak,
)
from tycho.runtime.messages import Result, Status, Trace
from tycho.runtime.planning import (
    CHAIN_BEAT_SECONDS,
    Questions,
    Refusal,
    announced,
    needs_facts,
    plan_request,
)
from tycho.runtime.ports import (
    ActionLog,
    Brain,
    Display,
    Feedback,
    GameState,
    RecognitionUnavailable,
    Speaker,
    Transcriber,
)
from tycho.runtime.reporter import Reporter
from tycho.runtime.routing import (
    NotUnderstood,
    Picked,
    Reserved,
    from_brain,
    is_say_again,
    reply_to_question,
    route,
    tile_command,
)
from tycho.tile import Card
from tycho.tile.cards import long_answer, notice
from tycho.voice import SpeechError

log = logging.getLogger(__name__)

__all__ = [  # the word checks and timings tests and tools import from here
    "CANCEL_WORDS",
    "CHAIN_BEAT_SECONDS",
    "CONFIRM_WORDS",
    "FOLLOW_UP_LIMIT",
    "FOLLOW_UP_SECONDS",
    "SAY_AGAIN_WORDS",
    "TILE_WORDS",
    "Session",
    "is_say_again",
    "reply_to_question",
    "tile_command",
]

MIN_CLIP_SECONDS = 0.25
"""Shorter than this is a slip of the finger, not a command."""
TAIL_SECONDS = 0.15
"""The most audio kept after release: people let go as they finish the last word, and
the microphone delivers audio in blocks. The benchmark recordings were made this way."""
QUIET_TAIL_SECONDS = 0.06
"""The tail ends early once the clip has been this quiet: the player has finished.
On the recorded test set this ends most tails at release, saving about 0.12 s (#58)."""
TAIL_CHECK_SECONDS = 0.01

CONFIRM_SECONDS = 5.0
"""How long a question waits for "confirm", unless the settings say otherwise."""
POLL_RETRY_SECONDS = 1.0
"""How long to wait before reading push-to-talk again after it failed."""

BRAIN_SECONDS = 8.0
"""How long to wait for the brain before saying it couldn't answer (ADR 14)."""
KEEP_CHARACTERS = 40
"""Lines this short ("Gear down.", "Which Constellation?") repeat, so the speaker
keeps their audio: the next time they're free and start at once."""
SEARCH_SECONDS = 12.0
"""Once a web search starts, the brain has this long from then (searches take 4-8 s)."""
LOOKING_SECONDS = 7.0
"""As each other look-up starts ("Checking." is said for the first), the brain has at
least this long from then: a look-up and Claude's answer to it take 3-5 s, and two in
a row ran past BRAIN_SECONDS in the play session (#150). Never less than
BRAIN_SECONDS, and never past LOOKING_LIMIT."""
SEARCH_LIMIT = 20.0
OPEN_SEARCH_SECONDS = 27.0
OPEN_SEARCH_LIMIT = 32.0
"""With ``web_search_open`` (#354, on by default): two searches and page reads take longer,
up to the search's own 25 s, so the brain gets more time once it starts."""
"""A request with a web search answers or fails this long after it was asked."""
LOOKING_LIMIT = 15.0
"""However many look-ups, an answer comes or fails this long after the request."""
FILLERS = ("Let me check.", "Checking the latest.", "One moment, looking that up.")
"""Said while a web search runs (about 4 s), taking turns, so the wait isn't
silence (ADR 15). Kept as audio after the first time, so they start at once."""
CHECKING = ("Checking.", "Looking that up.", "One moment.")
"""Said when any other look-up starts (prices, routes, a ship): Claude takes 1-4 s
to choose one, and the answer can take as long again. Kept as audio, like FILLERS,
so they cost the voice service once."""

_RECEIPT_STATUS: dict[str, Status] = {
    "cancelled": "cancelled",
    "no_focus": "no_focus",
    "failed": "failed",
}


@dataclass(frozen=True, slots=True)
class _Question:
    """A confirm-grade request waiting for the player's "confirm"."""

    request: int
    commands: tuple[Command, ...]
    deadline: float
    by_brain: bool = False
    say: str = ""


@dataclass(slots=True)
class _Progress:
    """How far one request got, so even a cancelled one is logged with what it pressed."""

    commands: tuple[str, ...] = ()
    pressed: list[str] = field(default_factory=list[str])
    confirmed: bool = False
    by_brain: bool = False
    say: str = ""
    announced: bool = False
    question: str = ""
    keydown_ms: float | None = None
    card: Card | None = None


def _log_failure(task: asyncio.Task[None]) -> None:
    """Put a background warm-up's failure in Tycho's log, not asyncio's stderr warning."""
    if not task.cancelled() and (e := task.exception()) is not None:
        log.error("Warming up failed: %s", e, exc_info=e)


class Session:
    def __init__(
        self,
        *,
        audio: AudioSource,
        push_to_talk: PushToTalk,
        transcriber: Transcriber,
        lexicon: Lexicon,
        matcher: CommandMatcher,
        keybinds: Callable[[], Keybinds],
        presser: Presser,
        feedback: Feedback,
        game_state: GameState | None = None,
        action_log: ActionLog | None = None,
        brain: Brain | None = None,
        brain_seconds: float = BRAIN_SECONDS,
        search_seconds: float = SEARCH_SECONDS,
        search_limit: float = SEARCH_LIMIT,
        looking_seconds: float = LOOKING_SECONDS,
        looking_limit: float = LOOKING_LIMIT,
        speaker: Speaker | None = None,
        confirm_seconds: float = CONFIRM_SECONDS,
        samplerate: int = 16_000,
        log_transcripts: bool = False,
        on_result: Callable[[Result], None] | None = None,
        poll_interval: float = 0.005,
        tail_seconds: float = TAIL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        address: Callable[[], str] = lambda: "",
        display: Display | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        safety_lists: Safety | None = None,
    ) -> None:
        """``safety_lists`` are the game pack's lists for the safety gate (Star Citizen's
        when None): every plan and every brain pick uses them (ADR 18)."""
        self._safety_lists = safety_lists
        self._tail_seconds = tail_seconds
        self._tail_pending = False
        self._audio = audio
        self._ptt = push_to_talk
        self._transcriber = transcriber
        self._lexicon = lexicon
        self._matcher = matcher
        self._keybinds = keybinds
        self._presser = presser
        self._feedback = feedback
        self._game_state = game_state
        self._reporter = Reporter(log_transcripts=log_transcripts, action_log=action_log)
        self._brain = brain
        self._brain_seconds = brain_seconds
        self._search_seconds = search_seconds
        self._search_limit = search_limit
        self._looking_seconds = looking_seconds
        self._looking_limit = looking_limit
        self._speaker = speaker
        self._told_keys_off = False
        """Whether this session has said why a press was refused with key actions off."""
        self._speech: asyncio.Task[None] | None = None
        self._filling: asyncio.Task[None] | None = None
        """The filler line, while it plays; the answer waits for it rather than cut it off."""
        self._fillers = itertools.cycle(FILLERS)
        self._checking = itertools.cycle(CHECKING)
        self._speech_warming: asyncio.Task[None] | None = None
        self._brain_warming: asyncio.Task[None] | None = None
        self._conversation = Conversation(clock)
        self._confirm_seconds = confirm_seconds
        self._question: _Question | None = None
        self._samplerate = samplerate
        self._on_result = on_result
        self._address = address
        """What to call the player, at the end of a confirm question (#15)."""
        self._display = display
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        """The wait between a sequence's steps; tests pass one that only records it (#61)."""
        self._ids = itertools.count(1)
        self._listening = False
        self._current: asyncio.Task[Result] | None = None
        self._cancel = threading.Event()
        self._stt = ThreadPoolExecutor(1, thread_name_prefix="tycho-stt")
        self._keys = ThreadPoolExecutor(1, thread_name_prefix="tycho-keys")
        self._closed = False
        self._paused = False

    def pause(self, paused: bool) -> None:
        """Paused, push-to-talk does nothing, from the next press (the tray, ADR 17).
        A press already held still ends normally, so the microphone never stays open."""
        self._paused = paused
        busy = self._listening or (self._current is not None and not self._current.done())
        if paused and not busy and self._display is not None:
            self._display.idle()  # a request still running puts the light out itself

    @property
    def paused(self) -> bool:
        return self._paused

    def set_confirm_seconds(self, seconds: float) -> None:
        """How long to wait for "confirm", from the next question on (#111)."""
        self._confirm_seconds = seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Listen for push-to-talk until ``stop`` is set, then shut down safely."""
        loop = asyncio.get_running_loop()
        halt = threading.Event()
        poller = threading.Thread(
            target=self._poll, args=(loop, halt), name="tycho-ptt", daemon=True
        )
        poller.start()
        log.debug("Listening for push-to-talk on %s", self._ptt.binding)
        self.warm_brain()
        self._warm_speaker()
        try:
            await stop.wait()
        finally:
            halt.set()
            await asyncio.to_thread(poller.join, 1.0)
            await self.close()

    def warm_brain(self) -> None:
        """Warm the brain's cache in the background, at the start and after the keybinds
        change (#60), so the next request isn't about 3 s cold. A failure is logged."""
        if self._brain is None or self._closed:
            return
        if self._brain_warming is not None:
            self._brain_warming.cancel()  # for the old keybinds
        self._brain_warming = asyncio.create_task(self._brain.warm(self._keybinds()))
        self._brain_warming.add_done_callback(_log_failure)

    def handle(self, edge: Edge) -> None:
        """React to push-to-talk. Runs on the event loop."""
        if self._closed:
            return
        if edge is Edge.PRESSED:
            if self._paused:
                # Paused means nothing new starts. Push-to-talk still stops whatever
                # is running, as it always does: a key sequence, or Tycho talking.
                self._cancel_current()
                self._stop_speaking()
                return
            if self._tail_pending:  # the last release is still recording its tail: drop it
                self._tail_pending = False
                with contextlib.suppress(AudioError):
                    self._audio.stop()
            self._cancel_current()
            self._stop_speaking()  # the player is talking: Tycho stops at once
            try:
                self._audio.start()
            except AudioError as e:
                log.error("The microphone stopped working: %s", e)
                self._feedback.not_done()
                return
            self._listening = True
            if self._display is not None:
                self._display.listening()
        elif self._listening:
            self._listening = False
            self._tail_pending = True
            if self._display is not None:
                self._display.thinking()
            self._current = asyncio.create_task(self._serve(next(self._ids)))

    async def idle(self) -> Result | None:
        """Wait for the request in flight, if any, and for Tycho to finish saying
        anything about it. Returns how the request ended."""
        task = self._current
        if task is None:
            return None
        await asyncio.gather(task, return_exceptions=True)
        if self._speech is not None:
            await asyncio.gather(self._speech, return_exceptions=True)
        return None if task.cancelled() else task.result()

    async def close(self) -> None:
        """Cancel in-flight work, and wait until every held key has been released."""
        if self._closed:
            return
        self._closed = True
        if self._listening or self._tail_pending:
            self._listening = self._tail_pending = False
            with contextlib.suppress(AudioError):
                self._audio.stop()
        self._cancel_current()
        speech = self._speech
        self._stop_speaking()  # before waiting: idle() would wait for the line to end
        await self.idle()
        if speech is not None:
            await asyncio.gather(speech, return_exceptions=True)
        for warming in (self._speech_warming, self._brain_warming):
            if warming is not None:
                warming.cancel()
                await asyncio.gather(warming, return_exceptions=True)
        if self._brain is not None:
            with contextlib.suppress(Exception):
                await self._brain.close()
        if self._speaker is not None:
            with contextlib.suppress(Exception):
                await self._speaker.close()
        # The key worker returns within ~10 ms of a cancel, having let go of every key.
        await asyncio.to_thread(self._keys.shutdown, True)
        self._stt.shutdown(wait=False, cancel_futures=True)
        log.info("Stopped. No keys are held.")

    def _poll(self, loop: asyncio.AbstractEventLoop, halt: threading.Event) -> None:
        failing = ""
        while not halt.is_set():
            # Not halt.wait(interval): on Windows that rounds up to the 15.6 ms timer
            # tick, adding about 8 ms to every press and release. time.sleep doesn't.
            time.sleep(self._poll_interval)
            try:
                edge = self._ptt.poll()
            except Exception as e:  # a joystick error must never silently end push-to-talk
                if str(e) != failing:  # log each new problem once, not 200 times a second
                    failing = str(e)
                    log.exception("Reading the push-to-talk button failed; trying again")
                halt.wait(POLL_RETRY_SECONDS)
                continue
            if failing:
                log.info("Push-to-talk is working again")
                failing = ""
            if edge is not None:
                try:
                    loop.call_soon_threadsafe(self.handle, edge)
                except RuntimeError:  # the loop closed underneath us
                    return

    async def _tail(self) -> None:
        """Wait up to the tail length, ending as soon as the clip goes quiet."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._tail_seconds
        while self._audio.quiet_seconds() < QUIET_TAIL_SECONDS:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(TAIL_CHECK_SECONDS, remaining))

    def _cancel_current(self) -> None:
        self._cancel.set()
        if self._current is not None and not self._current.done():
            self._current.cancel()

    async def _serve(self, request: int) -> Result:
        trace = Trace(self._clock)
        cancel = self._cancel = threading.Event()
        progress = _Progress()
        try:
            # Keep listening briefly after release, so a last word still in flight isn't
            # cut, unless the player had already finished.
            await self._tail()
            self._tail_pending = False
            clip = self._audio.stop()
            trace.mark("tail")
            result = await self._process(request, clip, trace, cancel, progress)
        except asyncio.CancelledError:
            cancel.set()
            self._report(self._result(request, "cancelled", trace, progress=progress))
            raise
        except AudioError as e:
            log.error("The microphone stopped working: %s", e)
            result = self._result(request, "failed", trace, detail=str(e), progress=progress)
        except RecognitionUnavailable as e:  # expected, and the transcriber says why: no traceback
            result = self._result(request, "failed", trace, detail=str(e), progress=progress)
        except Exception as e:  # one request's failure never stops the loop
            log.exception("Request %d failed", request)
            detail = f"{type(e).__name__}: {e}"
            result = self._result(request, "failed", trace, detail=detail, progress=progress)
        self._report(result)
        return result

    async def _process(
        self,
        request: int,
        clip: Samples,
        trace: Trace,
        cancel: threading.Event,
        progress: _Progress,
    ) -> Result:
        if len(clip) / self._samplerate < MIN_CLIP_SECONDS:
            return self._result(request, "empty", trace, detail="too short")

        loop = asyncio.get_running_loop()
        # Only the speech: the encoder's time grows with the clip, and players often
        # hold the button half a second before they start talking.
        speech = trim_silence(clip, self._samplerate)
        heard = await loop.run_in_executor(
            self._stt, self._transcriber.transcribe, speech, self._samplerate
        )
        trace.mark("transcribe")
        if not heard.strip():
            return self._result(request, "empty", trace, detail="nothing heard")

        text = self._lexicon.correct(heard).text
        trace.mark("correct")
        # A question lasts until the player next says something, whatever it is,
        # except "say that again", which asks it again (ADR 13, amended for #226).
        question, self._question = self._question, None
        if question is not None and self._display is not None:
            self._display.answered()
        routed = route(
            text,
            self._matcher,
            tile=self._display is not None and self._display.on,
            brain=self._brain is not None,
        )
        trace.mark("match")
        if isinstance(routed, Reserved):
            word = routed.word
            if word == "say_again":
                return self._say_again(request, trace, heard, text, question)
            if word == "confirm" or word == "cancel":
                if question is None or self._clock() > question.deadline:
                    detail = f"nothing to {word}" if question is None else "the question expired"
                    if word == "cancel":
                        # "Never mind" with nothing to cancel is still heard (#351): no
                        # not-done sound, and the talk so far is let go.
                        progress.commands = ("cancel",)
                        return self._result(
                            request, "done", trace, heard, text, detail, progress=progress
                        )
                    return self._result(request, "not_understood", trace, heard, text, detail)
                if word == "cancel":
                    progress.commands = ("cancel",)  # never logged as the cancelled command
                    asked = "+".join(command.id for command in question.commands)
                    detail = f"cancelled #{question.request} ({asked})"
                    return self._result(
                        request, "done", trace, heard, text, detail, progress=progress
                    )
                commands, confirmed = question.commands, True
                progress.by_brain, progress.say = question.by_brain, question.say
            else:
                return self._tile(request, word, trace, heard, text)
        elif isinstance(routed, NotUnderstood):
            return self._result(request, "not_understood", trace, heard, text, routed.reason)
        elif isinstance(routed, Picked):
            commands, confirmed = routed.commands, False
        else:
            confirmed = False
            progress.by_brain = True
            picked = await self._ask_brain(text)
            trace.mark("brain")
            if isinstance(picked, Reply):
                progress.say, progress.card = picked.text, picked.card
                return self._result(request, "replied", trace, heard, text, progress=progress)
            if isinstance(picked, str):
                detail = f"the brain couldn't answer: {picked}"
                return self._result(
                    request, "failed", trace, heard, text, detail, progress=progress
                )
            commands, progress.say = picked.commands, picked.say

        progress.commands = tuple(command.id for command in commands)
        progress.confirmed = confirmed

        def end(status: Status, detail: str = "") -> Result:
            return self._result(request, status, trace, heard, text, detail, progress=progress)

        # Plan the whole chain before pressing anything (planning.py). A confirmation
        # plans again from scratch, with the keybinds as they are now.
        keybinds = self._keybinds()
        progress.announced = announced(commands)
        facts: frozenset[str] = frozenset()
        if self._game_state is not None and needs_facts(commands):
            facts = await asyncio.to_thread(self._game_state.facts)
        planned = plan_request(
            commands, keybinds, facts, confirmed=confirmed, lists=self._safety_lists
        )
        if isinstance(planned, Refusal):
            return end(planned.status, planned.detail)
        trace.mark("gate")
        if isinstance(planned, Questions):
            progress.question = planned.question(self._address())
            deadline = self._clock() + self._confirm_seconds
            self._question = _Question(
                request, tuple(commands), deadline, progress.by_brain, progress.say
            )
            return end("asking", planned.detail)

        def first_key_down() -> None:  # on the key thread, as the key goes down
            if progress.keydown_ms is None:
                progress.keydown_ms = trace.elapsed_ms()

        for wait, press in planned.presses:
            if wait:
                await self._sleep(wait)
            running = loop.run_in_executor(
                self._keys, self._presser.press, press, cancel, first_key_down
            )
            try:
                receipt = await asyncio.shield(running)
            except asyncio.CancelledError:
                # The press runs on in its thread until it has let go (``cancel`` is set,
                # so that's soon). Wait for it, so the log says what reached the game.
                if (await running).sent:
                    progress.pressed.append(press.action)
                raise
            if not receipt.done:
                if receipt.sent:
                    progress.pressed.append(press.action)
                trace.mark("press")
                return end(_RECEIPT_STATUS.get(receipt.outcome, "failed"), receipt.detail)
            progress.pressed.append(press.action)
        trace.mark("press")
        return end("done")

    async def _ask_brain(self, text: str) -> Picked | Reply | str:
        """What the brain decides, within its time (ADR 14, #150, #169): commands, a
        reply, or why it couldn't answer."""
        assert self._brain is not None  # noqa: S101 - only routed here with a brain
        loop = asyncio.get_running_loop()
        self._warm_speaker()  # while the brain thinks, so speech starts sooner
        try:
            async with asyncio.timeout(self._brain_seconds) as budget:
                asked = loop.time()

                def searching() -> None:
                    # Capped (#169): after look-ups, a search can't run past the limit.
                    # Never earlier than brain_seconds, though (#314): a long one stands.
                    when = budget.when()
                    later = loop.time() + self._search_seconds
                    cap = asked + max(self._search_limit, self._brain_seconds)
                    budget.reschedule(min(max(later, when or later), cap))
                    self._fill()

                def looking() -> None:
                    # Only ever later, and never past the limit: an early look-up
                    # keeps the usual deadline, and each one after moves it on.
                    later = min(loop.time() + self._looking_seconds, asked + self._looking_limit)
                    if (when := budget.when()) is None or later > when:
                        budget.reschedule(later)

                decision = await self._brain.decide(
                    BrainRequest(
                        text,
                        self._keybinds(),
                        self._conversation.follow_ups(),
                        waiting=searching,
                        looking=looking,
                        checking=lambda: self._fill(self._checking),
                    )
                )
            return from_brain(decision, self._matcher, self._safety_lists)
        except (BrainError, TimeoutError) as e:
            return str(e) or "it took too long"

    def _tile(self, request: int, what: str, trace: Trace, heard: str, text: str) -> Result:
        """Keep, clear or bring back a card, or show the last answer to read."""
        display = self._display
        assert display is not None  # noqa: S101 - only called with a display
        if what == "pin":
            done = display.pin()
        elif what == "again":
            done = display.again()
        elif what == "clear":
            display.clear()
            done = True
        else:
            said, card = self._conversation.last_reply
            if card is not None:
                done = display.show(card, pinned=True)
            else:
                done = bool(said) and display.show(long_answer(said))
        progress = _Progress(commands=(f"tile_{what}",))
        if done:
            return self._result(request, "done", trace, heard, text, progress=progress)
        detail = "nothing on the Tile for that" if what != "show" else "nothing to show"
        return self._result(
            request, "not_understood", trace, heard, text, detail, progress=progress
        )

    def _say_again(
        self, request: int, trace: Trace, heard: str, text: str, question: _Question | None
    ) -> Result:
        """The last line Tycho said, word for word, with no call to the brain (#222): an
        answer, or the line about what it did (#226). Only ever spoken: nothing is
        planned or pressed, and a card isn't redrawn (that's "show that again").

        A confirm question still open is asked again, and waits afresh for "confirm"
        (ADR 13, amended for #226). One that expired or was answered isn't: "Say
        confirm." would then ask for something that does nothing."""
        line = self._conversation.last_line
        if line.question is not None:
            progress = _Progress(commands=("say_again",))
            if question is None or question.request != line.question:
                detail = "the question has ended"
            elif self._clock() > question.deadline:
                detail = "the question expired"
            else:
                deadline = self._clock() + self._confirm_seconds
                self._question = replace(question, deadline=deadline)
                progress.question = line.text
                detail = 'asked again: hold push-to-talk and say "confirm"'
                return self._result(
                    request, "asking", trace, heard, text, detail, progress=progress
                )
            return self._result(
                request, "not_understood", trace, heard, text, detail, progress=progress
            )
        said = line.text.strip()
        progress = _Progress(commands=("say_again",), say=said)
        if not said:
            return self._result(
                request,
                "not_understood",
                trace,
                heard,
                text,
                "nothing to say again",
                progress=progress,
            )
        return self._result(request, "done", trace, heard, text, progress=progress)

    def _result(
        self,
        request: int,
        status: Status,
        trace: Trace,
        heard: str = "",
        text: str = "",
        detail: str = "",
        *,
        progress: _Progress | None = None,
    ) -> Result:
        progress = progress or _Progress()
        return Result(
            request=request,
            status=status,
            heard=heard,
            text=text,
            pressed=tuple(progress.pressed),
            detail=detail,
            commands=progress.commands,
            confirmed=progress.confirmed,
            by_brain=progress.by_brain,
            say=progress.say,
            announced=progress.announced,
            question=progress.question,
            card=progress.card,
            timings=dict(trace.stages),
            total_ms=trace.total_ms,
            keydown_ms=progress.keydown_ms,
        )

    def _report(self, result: Result) -> None:
        speak = self._speaker is not None and would_speak(result)
        self._reporter.report(result, voice=self._speaker is not None, speak=speak)
        self._conversation.heard(result)
        self._to_tile(result)
        if self._keys_off_first(result):
            # A player who forgot they turned key actions off hears why, once a session
            # (#327): on the Tile, and spoken after the not-done sound.
            if self._display is not None:
                self._display.show(notice(result.detail))
            self._feedback.not_done()
            if self._speaker is not None:
                self._start_speaking(self._speaker, result.detail, lambda: None)
        elif self._speaker is not None and speak:
            self._start_speaking(self._speaker, result.say, self._feedback.done)
        elif result.status == "asking" and self._speaker is not None and result.question:
            self._start_speaking(self._speaker, result.question, self._feedback.ask)
        elif result.done or result.status == "replied":
            self._feedback.done()
        elif result.status == "asking":
            self._feedback.ask()
        elif result.status != "cancelled":  # a barge-in: the player is already talking again
            self._feedback.not_done()
        if self._on_result is not None:
            self._on_result(result)

    def _keys_off_first(self, result: Result) -> bool:
        """Whether this is the session's first press refused because key actions are off."""
        lists = self._safety_lists
        if self._told_keys_off or lists is None or not lists.keys_off:
            return False
        if result.status != "refused" or result.detail != lists.keys_off:
            return False
        self._told_keys_off = True
        return True

    def _to_tile(self, result: Result) -> None:
        """The talk light goes out, and a reply's card or the gate's question goes up."""
        display = self._display
        if display is None:
            return
        if not self._listening:  # a barge-in: the light is already the new request's
            display.idle()
        if result.status == "replied":
            if result.card is not None:
                display.show(result.card)
        elif result.status == "asking" and result.question:
            display.ask(result.question, self._confirm_seconds)

    def _start_speaking(self, speaker: Speaker, text: str, instead: Callable[[], None]) -> None:
        """Say ``text``, after the filler line if one is playing; if nothing can be
        spoken, play the ``instead`` sound."""
        filler = self._filling if self._speech is self._filling else None
        if filler is None or filler.done():
            filler = None
            self._stop_speaking()
        self._filling = None
        keep = len(text.strip()) <= KEEP_CHARACTERS
        self._speech = asyncio.create_task(
            self._speak(speaker, text, instead, keep=keep, after=filler)
        )

    def _fill(self, lines: Iterator[str] | None = None) -> None:
        """A look-up has started (a web search, unless ``lines`` says): say a short
        line while it runs."""
        if self._speaker is None or self._closed:
            return
        self._stop_speaking()
        line = next(lines or self._fillers)
        self._speech = self._filling = asyncio.create_task(
            self._speak(self._speaker, line, lambda: None, keep=True)
        )

    async def _speak(
        self,
        speaker: Speaker,
        text: str,
        instead: Callable[[], None],
        *,
        keep: bool = False,
        after: asyncio.Task[None] | None = None,
    ) -> None:
        try:
            if after is not None:
                await asyncio.wait({after})  # never raises, even if it was cancelled
            await speaker.speak(text, keep=keep)
        except SpeechError as e:
            log.warning("Couldn't speak, so a sound instead: %s", e)
            instead()
        except Exception:  # speech failing must never stop the loop
            log.exception("Speaking failed")
            instead()
        finally:
            if after is not None and not after.done():
                after.cancel()  # stopped while waiting: the filler stops too

    def _stop_speaking(self) -> None:
        if self._speech is not None and not self._speech.done():
            self._speech.cancel()
        self._speech = None

    def _warm_speaker(self) -> None:
        if self._speaker is None or self._closed:
            return
        if self._speech_warming is None or self._speech_warming.done():
            self._speech_warming = asyncio.create_task(self._speaker.warm())
            self._speech_warming.add_done_callback(_log_failure)
