"""The talking loop: hold push-to-talk, speak, and Tycho presses the right keys (ADR 11).

One asyncio loop coordinates; nothing blocking runs on it:
- push-to-talk is polled on its own thread and handed over with call_soon_threadsafe;
- transcription has one worker thread;
- key presses have one worker thread, so only one press happens at a time.

One request at a time. Pressing push-to-talk again cancels whatever is in
flight, and a key sequence stops within about 10 ms with every key released.
"""

import asyncio
import contextlib
import itertools
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from tycho import safety
from tycho.audio.clip import Samples
from tycho.audio.source import AudioError, AudioSource
from tycho.commands import CommandMatcher, Defer, resolve
from tycho.game.keybinds import Keybinds
from tycho.input import NotPressable, Press, Presser, UnsupportedKey, plan
from tycho.lexicon import Lexicon
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime.messages import Result, Status, Trace
from tycho.runtime.ports import Feedback, Transcriber

log = logging.getLogger(__name__)

MIN_CLIP_SECONDS = 0.25
"""Shorter than this is a slip of the finger, not a command."""
CHAIN_BEAT_SECONDS = 0.15
"""A pause between the actions of a chain, so the game registers each one."""

_RECEIPT_STATUS: dict[str, Status] = {
    "cancelled": "cancelled",
    "no_focus": "no_focus",
    "failed": "failed",
}


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
        samplerate: int = 16_000,
        log_transcripts: bool = False,
        on_result: Callable[[Result], None] | None = None,
        poll_interval: float = 0.005,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._audio = audio
        self._ptt = push_to_talk
        self._transcriber = transcriber
        self._lexicon = lexicon
        self._matcher = matcher
        self._keybinds = keybinds
        self._presser = presser
        self._feedback = feedback
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
        log.info("Listening. Hold %s to talk.", self._ptt.binding)
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
            try:
                clip = self._audio.stop()
            except AudioError as e:
                log.error("The microphone stopped working: %s", e)
                self._feedback.not_done()
                return
            self._current = asyncio.create_task(self._serve(next(self._ids), clip))

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
        if self._listening:
            self._listening = False
            with contextlib.suppress(AudioError):
                self._audio.stop()
        self._cancel_current()
        await self.idle()
        # The key worker returns within ~10 ms of a cancel, having let go of every key.
        await asyncio.to_thread(self._keys.shutdown, True)
        self._stt.shutdown(wait=False, cancel_futures=True)

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

    async def _serve(self, request: int, clip: Samples) -> Result:
        trace = Trace(self._clock)
        cancel = self._cancel = threading.Event()
        try:
            result = await self._process(request, clip, trace, cancel)
        except asyncio.CancelledError:
            cancel.set()
            self._report(self._result(request, "cancelled", trace))
            raise
        except Exception as e:  # one request's failure never stops the loop
            log.exception("Request %d failed", request)
            result = self._result(request, "failed", trace, detail=f"{type(e).__name__}: {e}")
        self._report(result)
        return result

    async def _process(
        self, request: int, clip: Samples, trace: Trace, cancel: threading.Event
    ) -> Result:
        loop = asyncio.get_running_loop()
        if len(clip) / self._samplerate < MIN_CLIP_SECONDS:
            return self._result(request, "empty", trace, detail="too short")

        heard = await loop.run_in_executor(
            self._stt, self._transcriber.transcribe, clip, self._samplerate
        )
        trace.mark("transcribe")
        if not heard.strip():
            return self._result(request, "empty", trace, detail="nothing heard")

        text = self._lexicon.correct(heard).text
        trace.mark("correct")
        decision = self._matcher.match(text)
        trace.mark("match")
        if isinstance(decision, Defer):
            return self._result(request, "not_understood", trace, heard, text, decision.reason)

        # Plan the whole chain before pressing anything: if any part can't be done,
        # none of it is, so the ship is never left half-way through a request.
        keybinds = self._keybinds()
        presses: list[Press] = []
        for command in decision.commands:
            resolved = resolve(command, keybinds)
            if resolved.action is None:
                detail = f"{command.id}: none of {', '.join(command.actions)} has a key"
                return self._result(request, "no_key", trace, heard, text, detail)
            verdict = safety.check(command, resolved.action)
            if not verdict.allowed:
                return self._result(request, "refused", trace, heard, text, verdict.reason)
            try:
                presses.append(plan(resolved.action, keybinds.modes))
            except (NotPressable, UnsupportedKey) as e:
                return self._result(request, "no_key", trace, heard, text, str(e))
        trace.mark("gate")

        pressed: list[str] = []
        for position, press in enumerate(presses):
            if position:
                await asyncio.sleep(CHAIN_BEAT_SECONDS)
            receipt = await loop.run_in_executor(self._keys, self._presser.press, press, cancel)
            if not receipt.done:
                trace.mark("press")
                status = _RECEIPT_STATUS.get(receipt.outcome, "failed")
                return self._result(
                    request, status, trace, heard, text, receipt.detail, tuple(pressed)
                )
            pressed.append(press.action)
        trace.mark("press")
        return self._result(request, "done", trace, heard, text, pressed=tuple(pressed))

    def _result(
        self,
        request: int,
        status: Status,
        trace: Trace,
        heard: str = "",
        text: str = "",
        detail: str = "",
        pressed: tuple[str, ...] = (),
    ) -> Result:
        return Result(
            request, status, heard, text, pressed, detail, dict(trace.stages), trace.total_ms
        )

    def _report(self, result: Result) -> None:
        stages = ", ".join(f"{name} {ms:.0f}" for name, ms in result.timings.items())
        said = f' "{result.text}"' if self._log_transcripts and result.text else ""
        log.info(
            "#%d%s: %s%s in %.0f ms (%s)",
            result.request,
            said,
            result.status,
            f" ({result.detail})" if result.detail else "",
            result.total_ms,
            stages or "no stages",
        )
        if result.done:
            self._feedback.done()
        elif result.status != "cancelled":  # a barge-in: the player is already talking again
            self._feedback.not_done()
        if self._on_result is not None:
            self._on_result(result)
