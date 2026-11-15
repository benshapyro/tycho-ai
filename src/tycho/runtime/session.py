"""The talking loop: hold push-to-talk, speak, and Tycho presses the right keys (ADR 11).

One asyncio loop coordinates; nothing blocking runs on it: push-to-talk is polled on
its own thread (``listening``), transcription has one worker thread, and key presses
have another, so only one press happens at a time (``pressing``).

One request at a time. Pressing push-to-talk again cancels whatever is in flight, and
a key sequence stops within about 10 ms with every key released; Tycho stops talking
at once too (``speaking``, which also says who speaks when, ADR 14).

The session keeps the threads, cancellation and barge-in, and the open confirm
question (ADR 13). Where words go is ``routing``, what gets pressed is ``planning``
(both pure, so the brain eval grades the same code, #55), asking the brain and
answering the question is ``asking``, the log is the ``Reporter``, what was said
lately is the ``Conversation``, and the Tile's part is ``tiling``.
"""

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor

from tycho.audio.clip import Samples, trim_silence
from tycho.audio.source import AudioError, AudioSource
from tycho.brain import Reply
from tycho.commands import CommandMatcher
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
    Question,
    Times,
    answered,
    ask_brain,
    say_again,
)
from tycho.runtime.conversation import (
    Conversation,
    would_speak,
)
from tycho.runtime.listening import TAIL_SECONDS, poll, tail
from tycho.runtime.messages import Progress, Result, Status, Trace
from tycho.runtime.planning import (
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
from tycho.runtime.pressing import press_all
from tycho.runtime.reporter import Reporter
from tycho.runtime.routing import (
    NotUnderstood,
    Picked,
    Reserved,
    own_keys,
    route,
)
from tycho.runtime.speaking import Speaking, log_failure
from tycho.runtime.tiling import tile_word, to_tile
from tycho.tile.cards import notice

log = logging.getLogger(__name__)


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
    ) -> None:
        """``safety_lists`` are the game pack's lists for the safety gate: every plan and
        every brain pick uses them (ADR 18)."""
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
        self._times = Times(
            brain_seconds, search_seconds, search_limit, looking_seconds, looking_limit
        )
        self._speaker = speaker
        self._voice = Speaking(speaker)
        self._told_keys_off = False
        """Whether this session has said why a press was refused with key actions off."""
        self._brain_warming: asyncio.Task[None] | None = None
        self._conversation = Conversation(clock)
        self._confirm_seconds = confirm_seconds
        self._question: Question | None = None
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
                # Paused means nothing new starts. Push-to-talk still stops whatever
                # is running, as it always does: a key sequence, or Tycho talking.
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

    async def _serve(self, request: int) -> Result:
        trace = Trace(self._clock)
        cancel = self._cancel = threading.Event()
        progress = Progress()
        try:
            # Keep listening briefly after release, so a last word still in flight isn't
            # cut, unless the player had already finished.
            await tail(self._audio, self._tail_seconds)
            self._tail_pending = False
            clip = self._audio.stop()
            trace.mark("tail")
            result = await self._process(request, clip, trace, cancel, progress)
        except asyncio.CancelledError:
            cancel.set()
            self._report(progress.result(request, "cancelled", trace))
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
        self._report(result)
        return result

    async def _process(
        self,
        request: int,
        clip: Samples,
        trace: Trace,
        cancel: threading.Event,
        progress: Progress,
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
            keys=not self._safety_lists.keys_off,
        )
        trace.mark("match")
        if isinstance(routed, Reserved):
            word = routed.word
            if word == "say_again":
                return self._say_again(request, trace, heard, text, question)
            if word == "confirm" or word == "cancel":
                answer = answered(word, question, self._clock())
                if isinstance(answer, Answer):
                    progress.commands = answer.commands
                    return progress.result(
                        request, answer.status, trace, heard, text, answer.detail
                    )
                commands, confirmed = answer.commands, True
                progress.by_brain, progress.say = answer.by_brain, answer.say
            else:
                return self._tile(request, word, trace, heard, text)
        elif isinstance(routed, NotUnderstood):
            return Progress().result(request, "not_understood", trace, heard, text, routed.reason)
        elif isinstance(routed, Picked):
            commands, confirmed = routed.commands, False
        else:
            confirmed = False
            progress.by_brain = True
            keys = own_keys(routed.matched, self._keybinds()) if routed.matched else ""
            picked = await self._ask_brain(text, keys)
            trace.mark("brain")
            if isinstance(picked, Reply):
                progress.say, progress.card = picked.text, picked.card
                return progress.result(request, "replied", trace, heard, text)
            if isinstance(picked, str) and routed.matched:
                # Key actions are off and the brain failed (#384): the gate's refusal
                # still says why nothing was pressed, as it did before the brain heard it.
                picked = Picked(routed.matched)
                progress.by_brain = False
            if isinstance(picked, str):
                detail = f"the brain couldn't answer: {picked}"
                return progress.result(request, "failed", trace, heard, text, detail)
            commands, progress.say = picked.commands, picked.say

        progress.commands = tuple(command.id for command in commands)
        progress.confirmed = confirmed

        def end(status: Status, detail: str = "") -> Result:
            return progress.result(request, status, trace, heard, text, detail)

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
        )
        return end(status, detail)

    async def _ask_brain(self, text: str, own_keys: str = "") -> Picked | Reply | str:
        """What the brain decides, within its time (``asking.ask_brain``)."""
        assert self._brain is not None  # noqa: S101 - only routed here with a brain
        self._voice.warm()  # while the brain thinks, so speech starts sooner
        return await ask_brain(
            self._brain,
            text,
            self._keybinds(),
            self._conversation.follow_ups(),
            own_keys,
            times=self._times,
            matcher=self._matcher,
            lists=self._safety_lists,
            searching=self._voice.fill,
            checking=self._voice.check,
        )

    def _tile(self, request: int, what: str, trace: Trace, heard: str, text: str) -> Result:
        """A word to the Tile (``tiling.tile_word``)."""
        assert self._display is not None  # noqa: S101 - only called with a display
        detail = tile_word(self._display, what, self._conversation.last_reply)
        progress = Progress(commands=(f"tile_{what}",))
        status: Status = "not_understood" if detail else "done"
        return progress.result(request, status, trace, heard, text, detail)

    def _say_again(
        self, request: int, trace: Trace, heard: str, text: str, question: Question | None
    ) -> Result:
        """Tycho's last line again (``asking.say_again``)."""
        now = self._clock()
        answer = say_again(self._conversation.last_line, question, now, self._confirm_seconds)
        if answer.reopened is not None:
            self._question = answer.reopened
        progress = Progress(answer.commands, say=answer.say, question=answer.question)
        return progress.result(request, answer.status, trace, heard, text, answer.detail)

    def _report(self, result: Result) -> None:
        speak = self._speaker is not None and would_speak(result)
        self._reporter.report(result, voice=self._speaker is not None, speak=speak)
        self._conversation.heard(result)
        if self._display is not None:
            listening, wait = self._listening, self._confirm_seconds
            to_tile(self._display, result, listening=listening, confirm_seconds=wait)
        keys_off = self._keys_off_first(result)
        if keys_off and self._display is not None:
            # A player who forgot they turned key actions off hears why, once a session
            # (#327): on the Tile, and spoken after the not-done sound.
            self._display.show(notice(result.detail))
        self._voice.tell(result, self._feedback, speak=speak, keys_off=keys_off)
        if self._on_result is not None:
            self._on_result(result)

    def _keys_off_first(self, result: Result) -> bool:
        """Whether this is the session's first press refused because key actions are off."""
        lists = self._safety_lists
        if self._told_keys_off or not lists.keys_off:
            return False
        if result.status != "refused" or result.detail != lists.keys_off:
            return False
        self._told_keys_off = True
        return True
