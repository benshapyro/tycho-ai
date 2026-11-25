"""The talking loop: hold push-to-talk, speak, and Tycho presses the right keys (ADR 11).

Nothing blocking runs on the asyncio loop: push-to-talk is polled on its own thread
(``listening``), and transcription and key presses have a worker thread each (``pressing``).
One request at a time: push-to-talk cancels what's in flight, every key released within
about 10 ms, and stops Tycho talking (``speaking``, which says who speaks when, ADR 14).

The session keeps the threads, cancellation, barge-in and the confirm question (ADR 13).
Where words go is ``routing``, what gets pressed is ``planning`` (both pure, so the eval
grades the same code, #55), asking the brain is ``asking``, the log is the ``Reporter``,
the Tile's part is ``tiling``, looking is ``look`` and plotting a route is ``plotting``.
"""

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from tycho.audio.clip import Samples, trim_silence
from tycho.audio.source import AudioError, AudioSource
from tycho.brain import Reply
from tycho.commands import CommandMatcher
from tycho.commands.keys import own_keys
from tycho.input import Presser
from tycho.keybinds import Keybinds
from tycho.lexicon import Lexicon
from tycho.packs import Safety
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime.asking import (
    BRAIN_SECONDS,
    CONFIRM_SECONDS,
    LOOKING_LIMIT,
    LOOKING_SECONDS,
    SEARCH_LIMIT,
    SEARCH_SECONDS,
    Answer,
    Asker,
    Question,
    Times,
    answered,
    say_again,
)
from tycho.runtime.conversation import Conversation, would_speak
from tycho.runtime.listening import TAIL_SECONDS, poll, tail
from tycho.runtime.look import ANSWER_ONLY, LOOK, Eyes, Unseen, look, wants_a_look
from tycho.runtime.messages import Progress, Result, Status, Trace
from tycho.runtime.planning import (
    Questions,
    Refusal,
    announced,
    needs_facts,
    plan_request,
    toggle_receipt,
)
from tycho.runtime.plotting import Plotter
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
from tycho.runtime.pressing import press_all
from tycho.runtime.reporter import Reporter
from tycho.runtime.routing import NotUnderstood, Picked, Reserved, route
from tycho.runtime.speaking import Speaking, Telling, log_failure, refused_keys_off
from tycho.runtime.tiling import tile_request, to_tile
from tycho.tile.cards import notice

log = logging.getLogger(__name__)
type Job = tuple[threading.Event, bool, Progress, int, Trace, str, str]  # for _plot


MIN_CLIP_SECONDS = 0.25
"""Shorter than this is a slip of the finger, not a command."""


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
        safety_lists: Safety,
        eyes: Eyes | None = None,
        looking: bool = False,
        plotter: Plotter | None = None,
    ) -> None:
        """``safety_lists``: the gate's lists (ADR 18). ``eyes``, ``looking``: #31. Routes: #588."""
        self._eyes, self._looking, self._plotter = eyes, looking, plotter
        self._safety_lists, self._confirm_seconds = safety_lists, confirm_seconds
        self._tail_seconds, self._tail_pending = tail_seconds, False
        self._audio, self._ptt, self._transcriber = audio, push_to_talk, transcriber
        self._lexicon, self._matcher, self._keybinds = lexicon, matcher, keybinds
        self._presser, self._feedback = presser, feedback
        self._game_state, self._brain, self._speaker = game_state, brain, speaker
        self._reporter = Reporter(log_transcripts=log_transcripts, action_log=action_log)
        self._voice = Speaking(speaker)
        self._telling = Telling(display, self._voice, feedback)
        self._told_keys_off = False
        """Whether this session has said why a press was refused with key actions off."""
        self._brain_warming: asyncio.Task[None] | None = None
        self._conversation = Conversation(clock)
        times = Times(brain_seconds, search_seconds, search_limit, looking_seconds, looking_limit)
        asks = (brain, times, matcher, safety_lists, self._voice, keybinds)
        lately, may = self._conversation.follow_ups, bool(eyes and looking)
        self._asker = Asker(*asks, lately, may, game_state)  # where the player is (#656)
        self._question: Question | None = None
        self._samplerate, self._on_result = samplerate, on_result
        self._address = address
        """What to call the player, at the end of a confirm question (#15)."""
        self._display, self._poll_interval, self._clock = display, poll_interval, clock
        self._sleep = sleep
        """The wait between a sequence's steps; tests pass one that only records it (#61)."""
        self._ids, self._listening = itertools.count(1), False
        self._current: asyncio.Task[Result] | None = None
        self._cancel = threading.Event()
        self._stt = ThreadPoolExecutor(1, thread_name_prefix="tycho-stt")
        self._keys = ThreadPoolExecutor(1, thread_name_prefix="tycho-keys")
        self._closed = self._paused = self._trying = False

    def try_it(self, on: bool) -> bool:
        """Setup's Try it (ADR 17): plan presses, send none. A change drops a question."""
        if on == self._trying:
            return False
        self._trying, self._question = on, None
        return True

    def pause(self, paused: bool) -> None:
        """Paused, push-to-talk does nothing from the next press; one held ends normally."""
        self._paused = paused
        busy = self._listening or (self._current is not None and not self._current.done())
        if paused and not busy and self._display is not None:
            self._display.idle()  # a request still running puts the light out itself

    @property
    def paused(self) -> bool:
        return self._paused

    def set_looking(self, on: bool) -> None:
        """Looking at the game window turned on or off, from the next request (#31)."""
        self._looking = on
        self._asker = replace(self._asker, may_look=self._eyes is not None and on)

    def set_confirm_seconds(self, seconds: float) -> None:
        """How long to wait for "confirm", from the next question on (#111)."""
        self._confirm_seconds = seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Listen for push-to-talk until ``stop`` is set, then shut down safely."""
        loop = asyncio.get_running_loop()
        self._telling.start(loop)
        halt = threading.Event()
        poller = threading.Thread(
            target=poll,
            args=(lambda: self._ptt.poll(), self._poll_interval, halt, loop, self.handle),
            name="tycho-ptt",
            daemon=True,
        )
        poller.start()
        log.debug("Listening for push-to-talk on %s", self._ptt.binding)
        self.warm_brain()
        self._voice.warm()
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
        self._brain_warming.add_done_callback(log_failure)

    def handle(self, edge: Edge) -> None:
        """React to push-to-talk. Runs on the event loop."""
        if self._closed:
            return
        if edge is Edge.PRESSED:
            if self._paused:
                # Paused, nothing new starts, but push-to-talk still stops what's running.
                self._cancel_current()
                self._voice.stop()
                return
            if self._tail_pending:  # the last release is still recording its tail: drop it
                self._tail_pending = False
                with contextlib.suppress(AudioError):
                    self._audio.stop()
            self._cancel_current()
            self._voice.stop()  # the player is talking: Tycho stops at once
            try:
                self._audio.start()
            except AudioError as e:
                log.error("The microphone stopped working: %s", e)
                self._feedback.not_done()
                return
            self._listening = True
            if self._brain is not None:
                self._brain.prime()  # while the player talks (#21)
            if self._display is not None:
                self._display.listening()
        elif self._listening:
            self._listening = False
            self._tail_pending = True
            if self._display is not None:
                self._display.thinking()
            # Try it is decided as the button is let go (#467), never later.
            self._current = asyncio.create_task(self._serve(next(self._ids), self._trying))

    async def idle(self) -> Result | None:
        """Wait for the request in flight, if any, and for Tycho to finish saying it."""
        task = self._current
        if task is None:
            return None
        await asyncio.gather(task, return_exceptions=True)
        if self._voice.speech is not None:
            await asyncio.gather(self._voice.speech, return_exceptions=True)
        return None if task.cancelled() else task.result()

    async def close(self) -> None:
        """Cancel in-flight work, and wait until every held key has been released."""
        if self._closed:
            return
        self._closed = self._voice.closed = True
        if self._listening or self._tail_pending:
            self._listening = self._tail_pending = False
            with contextlib.suppress(AudioError):
                self._audio.stop()
        self._cancel_current()
        speech = self._voice.speech
        self._voice.stop()  # before waiting: idle() would wait for the line to end
        await self.idle()
        if speech is not None:
            await asyncio.gather(speech, return_exceptions=True)
        for warming in (self._voice.warming, self._brain_warming):
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

    def _cancel_current(self) -> None:
        self._cancel.set()
        if self._current is not None and not self._current.done():
            self._current.cancel()

    async def _serve(self, request: int, trying: bool = False) -> Result:
        trace = Trace(self._clock)
        cancel = self._cancel = threading.Event()
        progress = Progress()
        try:
            # Listen briefly after release, so a last word isn't cut, unless they'd finished.
            await tail(self._audio, self._tail_seconds)
            self._tail_pending = False
            clip = self._audio.stop()
            trace.mark("tail")
            result = await self._process(request, clip, trace, cancel, progress, trying)
        except asyncio.CancelledError:
            cancel.set()
            self._report(replace(progress.result(request, "cancelled", trace), tried=trying))
            raise
        except AudioError as e:
            log.error("The microphone stopped working: %s", e)
            result = progress.result(request, "failed", trace, detail=str(e))
        except RecognitionUnavailable as e:  # expected, and the transcriber says why: no traceback
            result = progress.result(request, "failed", trace, detail=str(e))
        except Exception as e:  # one request's failure never stops the loop
            log.exception("Request %d failed", request)
            detail = f"{type(e).__name__}: {e}"
            result = progress.result(request, "failed", trace, detail=detail)
        self._report(result := replace(result, tried=trying))
        return result

    async def _process(
        self,
        request: int,
        clip: Samples,
        trace: Trace,
        cancel: threading.Event,
        progress: Progress,
        trying: bool,
    ) -> Result:
        if len(clip) / self._samplerate < MIN_CLIP_SECONDS:
            return Progress().result(request, "empty", trace, detail="too short")

        loop = asyncio.get_running_loop()
        # Only the speech: the encoder's time grows with the clip, and players often
        # hold the button half a second before they start talking.
        speech = trim_silence(clip, self._samplerate)
        heard = await loop.run_in_executor(
            self._stt, self._transcriber.transcribe, speech, self._samplerate
        )
        trace.mark("transcribe")
        if not heard.strip():
            return Progress().result(request, "empty", trace, detail="nothing heard")

        corrected = self._lexicon.correct(heard)
        text = corrected.text
        trace.mark("correct")
        # A question lasts until the next words, but "say that again" asks it again (#226).
        question, self._question = self._question, None
        if question is not None and self._display is not None:
            self._display.answered()
        said = (request, trace, heard, text)
        job: Job = (cancel, trying, progress, *said)
        if self._plotter is not None and (place := await self._plotter.wanted(text)) is not None:
            return await self._plot(place, job)
        routed = route(
            text,
            self._matcher,
            tile=self._display is not None and self._display.on,
            brain=self._brain is not None,
            keys=not self._safety_lists.keys_off,
            look=wants_a_look(text, self._eyes, self._conversation.offered_look(), self._looking),
        )
        trace.mark("match")
        if isinstance(routed, Reserved):
            word = routed.word
            if word == "say_again":
                line, now = self._conversation.last_line, self._clock()
                answer = say_again(line, question, now, self._confirm_seconds)
                self._question = answer.reopened  # asked again: it waits afresh
                return answer.result(*said)
            if word == "confirm" or word == "cancel":
                answer = answered(word, question, self._clock())
                if isinstance(answer, Question) and answer.route:  # a guessed place (#650)
                    return await self._plot(answer.route, job, sure=True)
                if isinstance(answer, Answer):
                    progress.commands = answer.commands
                    return progress.result(
                        request, answer.status, trace, heard, text, answer.detail
                    )
                commands, confirmed = answer.commands, True
                progress.by_brain, progress.say = answer.by_brain, answer.say
            else:
                assert self._display is not None  # noqa: S101 - only routed with a display
                return tile_request(self._display, word, self._conversation.last_reply, *said)
        elif isinstance(routed, NotUnderstood):
            return Progress().result(request, "not_understood", trace, heard, text, routed.reason)
        elif isinstance(routed, Picked):
            commands, confirmed = routed.commands, False
        else:
            confirmed = False
            progress.by_brain = True
            keys = own_keys(routed.matched, self._keybinds()) if routed.matched else ""
            seen = await look(self._eyes, self._looking, self._feedback, text, routed.look)
            progress.commands = (LOOK,) if seen is not None else ()
            if isinstance(seen, Unseen):  # off, or no game window: nothing is sent
                return seen.result(progress, request, trace, heard, text)
            picked = await self._asker.ask(corrected.for_brain, keys, seen)
            trace.mark("brain")
            plain = seen is None and isinstance(picked, Reply) and not picked.looked_up
            if self._plotter is not None and (place := self._plotter.handed(plain)):  # #647
                return await self._plot(place, job)
            if isinstance(picked, Reply):
                progress.answered(picked.text, picked.card, picked.looked_up)
                return progress.result(request, "replied", trace, heard, text)
            if isinstance(picked, str) and routed.matched:
                # Keys off and the brain failed (#384): the gate's refusal still says why.
                picked = Picked(routed.matched)
                progress.by_brain = False
            if isinstance(picked, str):
                detail = f"the brain couldn't answer: {picked}"
                return progress.result(request, "failed", trace, heard, text, detail)
            if seen is not None:  # a look only answers (ADR 14): nothing is pressed
                return ANSWER_ONLY.result(progress, request, trace, heard, text)
            commands, progress.say = picked.commands, picked.say

        progress.commands = tuple(command.id for command in commands)
        progress.confirmed = confirmed

        def end(status: Status, detail: str = "") -> Result:
            return progress.result(request, status, trace, heard, text, detail)

        # Plan the whole chain before pressing (planning.py); a confirmation plans afresh.
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
            self._question = Question(
                request, tuple(commands), deadline, progress.by_brain, progress.say
            )
            return end("asking", planned.detail)

        status, detail = await press_all(
            planned.presses,
            presser=self._presser,
            keys=self._keys,
            cancel=cancel,
            sleep=self._sleep,
            progress=progress,
            trace=trace,
            trying=trying,
            still_trying=lambda: self._trying,
        )
        if status == "done" and planned.toggled and not trying and not progress.announced:
            line = toggle_receipt(planned.toggled, progress.say, others=planned.others)
            progress.say, progress.toggled = line, True
        return end(status, detail)

    def _report(self, result: Result) -> None:
        speak = self._speaker is not None and would_speak(result)
        self._reporter.report(result, voice=self._speaker is not None, speak=speak)
        self._conversation.heard(result)
        if self._display is not None:
            listening, wait = self._listening, self._confirm_seconds
            to_tile(self._display, result, listening=listening, confirm_seconds=wait)
        # The session's first press refused because key actions are off.
        keys_off = not self._told_keys_off and refused_keys_off(result, self._safety_lists)
        self._told_keys_off = self._told_keys_off or keys_off
        if keys_off and self._display is not None:
            # A player who forgot they turned key actions off hears why, once a session
            # (#327): on the Tile, and spoken after the not-done sound.
            self._display.show(notice(result.detail))
        self._voice.tell(result, self._feedback, speak=speak, keys_off=keys_off)
        if self._on_result is not None:
            self._on_result(result)

    async def _plot(self, place: str, job: Job, sure: bool = False) -> Result:
        """A route (#588). A guessed place is a confirm question first, like any (#650)."""
        plotter, (cancel, trying, progress, request, trace, heard, text) = self._plotter, job
        assert plotter is not None  # noqa: S101 - only called with a plotter
        status, detail = await plotter.answer(place, self._keys, cancel, trying, progress, sure)
        if status == "asking":
            deadline = self._clock() + self._confirm_seconds
            self._question = Question(request, (), deadline, route=plotter.asking.name)
        return progress.result(request, status, trace, heard, text, detail)

    def busy(self) -> bool:
        """Whether speaking up now would talk over someone (#4): push-to-talk is held, a
        request runs, Tycho is talking or waiting for "confirm", or it's paused."""
        asking = self._question is not None and self._clock() < self._question.deadline
        running = self._current is not None and not self._current.done()
        return self._listening or running or asking or self._voice.talking or self._paused

    def call_out(self, line: str) -> bool:
        """Speak up (#4), on the loop: said like any line, so push-to-talk stops it."""
        return self._telling.call_out(line)

    def tell(self, said: str) -> None:
        """From any thread: something the player must know that no request asked for,
        as the Tile or the tray icon stopping (#297; ``speaking.Telling``)."""
        self._telling.tell(said)
