"""The talking loop: hold push-to-talk, speak, and Tycho presses the right keys (ADR 11).

One asyncio loop coordinates; nothing blocking runs on it:
- push-to-talk is polled on its own thread and handed over with call_soon_threadsafe;
- transcription has one worker thread;
- key presses have one worker thread, so only one press happens at a time.

One request at a time. Pressing push-to-talk again cancels whatever is in
flight, and a key sequence stops within about 10 ms with every key released.

Confirm-grade requests are asked about (ADR 13): a question sound, then nothing
is pressed unless the next thing the player says is "confirm", within a few
seconds. Expiry is checked when they next speak; no timer acts on its own.
"""

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from tycho import safety
from tycho.audio.clip import Samples
from tycho.audio.source import AudioError, AudioSource
from tycho.commands import Command, CommandMatcher, Defer, resolve
from tycho.game.keybinds import Action, Keybinds
from tycho.input import NotPressable, Press, Presser, UnsupportedKey, plan
from tycho.lexicon import Lexicon
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime.messages import Result, Status, Trace
from tycho.runtime.ports import ActionLog, Feedback, GameState, Transcriber
from tycho.text import normalize

log = logging.getLogger(__name__)

MIN_CLIP_SECONDS = 0.25
"""Shorter than this is a slip of the finger, not a command."""
CHAIN_BEAT_SECONDS = 0.15
"""A pause between the actions of a chain, so the game registers each one."""
TAIL_SECONDS = 0.15
"""Audio kept after release: people let go as they finish the last word, and the
microphone delivers audio in blocks. The benchmark recordings were made this way."""

CONFIRM_SECONDS = 5.0
"""How long a question waits for "confirm", unless the settings say otherwise."""
CONFIRM_WORDS = frozenset({"confirm", "confirmed", "affirmative"})
CANCEL_WORDS = frozenset({"cancel", "abort", "negative", "belay that", "never mind", "nevermind"})
"""Replies to a question. Whole utterances only, and no command may use them as phrases."""

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


@dataclass(slots=True)
class _Progress:
    """How far one request got, so even a cancelled one is logged with what it pressed."""

    commands: tuple[str, ...] = ()
    pressed: list[str] = field(default_factory=list[str])
    confirmed: bool = False


def reply_to_question(text: str) -> str | None:
    """``"confirm"`` or ``"cancel"`` if the whole utterance is one of those replies."""
    said = " ".join(normalize(text))
    if said in CONFIRM_WORDS:
        return "confirm"
    if said in CANCEL_WORDS:
        return "cancel"
    return None


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
        confirm_seconds: float = CONFIRM_SECONDS,
        samplerate: int = 16_000,
        log_transcripts: bool = False,
        on_result: Callable[[Result], None] | None = None,
        poll_interval: float = 0.005,
        tail_seconds: float = TAIL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
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
        self._action_log = action_log
        self._confirm_seconds = confirm_seconds
        self._question: _Question | None = None
        self._samplerate = samplerate
        self._log_transcripts = log_transcripts
        self._on_result = on_result
        self._poll_interval = poll_interval
        self._clock = clock
        self._ids = itertools.count(1)
        self._listening = False
        self._current: asyncio.Task[Result] | None = None
        self._cancel = threading.Event()
        self._stt = ThreadPoolExecutor(1, thread_name_prefix="tycho-stt")
        self._keys = ThreadPoolExecutor(1, thread_name_prefix="tycho-keys")
        self._closed = False

    async def run(self, stop: asyncio.Event) -> None:
        """Listen for push-to-talk until ``stop`` is set, then shut down safely."""
        loop = asyncio.get_running_loop()
        halt = threading.Event()
        poller = threading.Thread(
            target=self._poll, args=(loop, halt), name="tycho-ptt", daemon=True
        )
        poller.start()
        log.debug("Listening for push-to-talk on %s", self._ptt.binding)
        try:
            await stop.wait()
        finally:
            halt.set()
            await asyncio.to_thread(poller.join, 1.0)
            await self.close()

    def handle(self, edge: Edge) -> None:
        """React to push-to-talk. Runs on the event loop."""
        if self._closed:
            return
        if edge is Edge.PRESSED:
            if self._tail_pending:  # the last release is still recording its tail: drop it
                self._tail_pending = False
                with contextlib.suppress(AudioError):
                    self._audio.stop()
            self._cancel_current()
            try:
                self._audio.start()
            except AudioError as e:
                log.error("The microphone stopped working: %s", e)
                self._feedback.not_done()
                return
            self._listening = True
        elif self._listening:
            self._listening = False
            self._tail_pending = True
            self._current = asyncio.create_task(self._serve(next(self._ids)))

    async def idle(self) -> Result | None:
        """Wait for the request in flight, if any, and return how it ended."""
        task = self._current
        if task is None:
            return None
        await asyncio.gather(task, return_exceptions=True)
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
        await self.idle()
        # The key worker returns within ~10 ms of a cancel, having let go of every key.
        await asyncio.to_thread(self._keys.shutdown, True)
        self._stt.shutdown(wait=False, cancel_futures=True)
        log.info("Stopped. No keys are held.")

    def _poll(self, loop: asyncio.AbstractEventLoop, halt: threading.Event) -> None:
        while not halt.wait(self._poll_interval):
            try:
                edge = self._ptt.poll()
            except OSError:
                log.exception("Reading the push-to-talk button failed")
                continue
            if edge is not None:
                try:
                    loop.call_soon_threadsafe(self.handle, edge)
                except RuntimeError:  # the loop closed underneath us
                    return

    def _cancel_current(self) -> None:
        self._cancel.set()
        if self._current is not None and not self._current.done():
            self._current.cancel()

    async def _serve(self, request: int) -> Result:
        trace = Trace(self._clock)
        cancel = self._cancel = threading.Event()
        progress = _Progress()
        try:
            # Keep listening briefly after release, so a last word still in flight isn't cut.
            await asyncio.sleep(self._tail_seconds)
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
        heard = await loop.run_in_executor(
            self._stt, self._transcriber.transcribe, clip, self._samplerate
        )
        trace.mark("transcribe")
        if not heard.strip():
            return self._result(request, "empty", trace, detail="nothing heard")

        text = self._lexicon.correct(heard).text
        trace.mark("correct")
        # A question lasts until the player next says something, whatever it is.
        question, self._question = self._question, None
        if (reply := reply_to_question(text)) is not None:
            trace.mark("match")
            if question is None or self._clock() > question.deadline:
                detail = f"nothing to {reply}" if question is None else "the question expired"
                return self._result(request, "not_understood", trace, heard, text, detail)
            if reply == "cancel":
                progress.commands = ("cancel",)  # never logged as the cancelled command itself
                asked = "+".join(command.id for command in question.commands)
                detail = f"cancelled #{question.request} ({asked})"
                return self._result(request, "done", trace, heard, text, detail, progress=progress)
            commands, confirmed = question.commands, True
        else:
            decision = self._matcher.match(text)
            trace.mark("match")
            if isinstance(decision, Defer):
                return self._result(request, "not_understood", trace, heard, text, decision.reason)
            commands, confirmed = decision.commands, False

        progress.commands = tuple(command.id for command in commands)
        progress.confirmed = confirmed

        def end(status: Status, detail: str = "") -> Result:
            return self._result(request, status, trace, heard, text, detail, progress=progress)

        # Plan the whole chain before pressing anything: if any part can't be done,
        # none of it is, so the ship is never left half-way through a request. A
        # confirmation plans again from scratch, with the keybinds as they are now.
        keybinds = self._keybinds()
        facts: frozenset[str] = frozenset()
        if self._game_state is not None and any(c.requires for c in commands):
            facts = await asyncio.to_thread(self._game_state.facts)
        steps: list[tuple[float, Press]] = []  # each press, and how long to wait before it
        questions: list[str] = []
        for command in commands:
            resolved = resolve(command, keybinds)
            if resolved.action is None:
                return end(
                    "no_key", f"{command.id}: none of {', '.join(command.actions)} has a key"
                )
            setup: list[Action] = []
            for name in command.setup:
                action = keybinds.get(name)
                if action is None or not action.bound:
                    return end("no_key", f"{command.id}: {name} needs a key of its own first")
                setup.append(action)
            for action in (*setup, resolved.action):
                verdict = safety.check(command, action, keybinds, facts, confirmed=confirmed)
                if verdict.ask:
                    questions.append(verdict.reason)
                elif not verdict.allowed:
                    return end("refused", verdict.reason)  # a refusal outranks any question
            try:
                for position, action in enumerate((*setup, resolved.action)):
                    settle = command.settle_seconds if setup and position == len(setup) else 0.0
                    beat = max(settle, CHAIN_BEAT_SECONDS) if steps else 0.0
                    steps.append((beat, plan(action, keybinds.modes)))
            except (NotPressable, UnsupportedKey) as e:
                return end("no_key", str(e))
        trace.mark("gate")
        if questions:
            deadline = self._clock() + self._confirm_seconds
            self._question = _Question(request, tuple(commands), deadline)
            return end("asking", f'{"; ".join(questions)}: hold push-to-talk and say "confirm"')

        for wait, press in steps:
            if wait:
                await asyncio.sleep(wait)
            receipt = await loop.run_in_executor(self._keys, self._presser.press, press, cancel)
            if not receipt.done:
                trace.mark("press")
                return end(_RECEIPT_STATUS.get(receipt.outcome, "failed"), receipt.detail)
            progress.pressed.append(press.action)
        trace.mark("press")
        return end("done")

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
            timings=dict(trace.stages),
            total_ms=trace.total_ms,
        )

    def _report(self, result: Result) -> None:
        stages = ", ".join(f"{name} {ms:.0f}" for name, ms in result.timings.items())
        said = f' "{result.text}"' if self._log_transcripts and result.text else ""
        log.info(
            "#%d%s: %s%s%s%s in %.0f ms (%s)",
            result.request,
            said,
            "+".join(result.commands) + " " if result.commands else "",
            "confirmed, " if result.confirmed else "",
            result.status,
            f" ({result.detail})" if result.detail else "",
            result.total_ms,
            stages or "no stages",
        )
        if result.commands and self._action_log is not None:
            self._record(self._action_log, result)
        if result.done:
            self._feedback.done()
        elif result.status == "asking":
            self._feedback.ask()
        elif result.status != "cancelled":  # a barge-in: the player is already talking again
            self._feedback.not_done()
        if self._on_result is not None:
            self._on_result(result)

    def _record(self, action_log: ActionLog, result: Result) -> None:
        entry: dict[str, object] = {
            "request": result.request,
            "commands": list(result.commands),
            "status": result.status,
            "pressed": list(result.pressed),
            "confirmed": result.confirmed,
        }
        if result.detail:
            entry["detail"] = result.detail
        if self._log_transcripts and result.text:
            entry["said"] = result.text
        try:
            action_log.record(entry)
        except OSError as e:  # a full disk mustn't stop the loop
            log.warning("Couldn't write the action log: %s", e)
