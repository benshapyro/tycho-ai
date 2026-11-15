"""Tycho talking (ADR 14): starting a line, the filler while a look-up runs, stopping at
once when the player presses push-to-talk, and warming the voice up. Out of
``session.py`` (#399); the session decides what is said and when.

Everything here runs on the event loop.
"""

import asyncio
import itertools
import logging
from collections.abc import Callable, Iterator

from tycho.runtime.messages import Result
from tycho.runtime.ports import Feedback, Speaker
from tycho.voice import SpeechError

log = logging.getLogger(__name__)

KEEP_CHARACTERS = 40
"""Lines this short ("Gear down.", "Which Constellation?") repeat, so the speaker
keeps their audio: the next time they're free and start at once."""
FILLERS = ("Let me check.", "Checking the latest.", "One moment, looking that up.")
"""Said while a web search runs (about 4 s), taking turns, so the wait isn't
silence (ADR 15). Kept as audio after the first time, so they start at once."""
CHECKING = ("Checking.", "Looking that up.", "One moment.")
"""Said when any other look-up starts (prices, routes, a ship): Claude takes 1-4 s
to choose one, and the answer can take as long again. Kept as audio, like FILLERS,
so they cost the voice service once."""


def log_failure(task: asyncio.Task[None]) -> None:
    """Put a background warm-up's failure in Tycho's log, not asyncio's stderr warning."""
    if not task.cancelled() and (e := task.exception()) is not None:
        log.error("Warming up failed: %s", e, exc_info=e)


class Speaking:
    """The one line Tycho is saying, if any. With no speaker, nothing is said."""

    def __init__(self, speaker: Speaker | None) -> None:
        self.speaker = speaker
        self.speech: asyncio.Task[None] | None = None
        """The line being said; ``idle`` and ``close`` wait for it."""
        self._filling: asyncio.Task[None] | None = None
        """The filler line, while it plays; the answer waits for it rather than cut it off."""
        self._fillers = itertools.cycle(FILLERS)
        self._checking = itertools.cycle(CHECKING)
        self.warming: asyncio.Task[None] | None = None
        self.closed = False

    def start(self, speaker: Speaker, text: str, instead: Callable[[], None]) -> None:
        """Say ``text``, after the filler line if one is playing; if nothing can be
        spoken, play the ``instead`` sound."""
        filler = self._filling if self.speech is self._filling else None
        if filler is None or filler.done():
            filler = None
            self.stop()
        self._filling = None
        keep = len(text.strip()) <= KEEP_CHARACTERS
        self.speech = asyncio.create_task(
            self._speak(speaker, text, instead, keep=keep, after=filler)
        )

    def tell(self, result: Result, feedback: Feedback, *, speak: bool, keys_off: bool) -> None:
        """Who speaks when (ADR 14): the line about how a request ended, or its sound.
        ``speak``: the result has a line to say. ``keys_off``: the session's first press
        refused with key actions off, said after the not-done sound (#327)."""
        speaker = self.speaker
        if keys_off:
            feedback.not_done()
            if speaker is not None:
                self.start(speaker, result.detail, lambda: None)
        elif speaker is not None and speak:
            self.start(speaker, result.say, feedback.done)
        elif result.status == "asking" and speaker is not None and result.question:
            self.start(speaker, result.question, feedback.ask)
        elif result.done or result.status == "replied":
            feedback.done()
        elif result.status == "asking":
            feedback.ask()
        elif result.status != "cancelled":  # a barge-in: the player is already talking again
            feedback.not_done()

    def fill(self, lines: Iterator[str] | None = None) -> None:
        """A look-up has started (a web search, unless ``lines`` says): say a short
        line while it runs."""
        if self.speaker is None or self.closed:
            return
        self.stop()
        line = next(lines or self._fillers)
        self.speech = self._filling = asyncio.create_task(
            self._speak(self.speaker, line, lambda: None, keep=True)
        )

    def check(self) -> None:
        """Any other look-up has started: "Checking." and the like."""
        self.fill(self._checking)

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

    def stop(self) -> None:
        if self.speech is not None and not self.speech.done():
            self.speech.cancel()
        self.speech = None

    def warm(self) -> None:
        if self.speaker is None or self.closed:
            return
        if self.warming is None or self.warming.done():
            self.warming = asyncio.create_task(self.speaker.warm())
            self.warming.add_done_callback(log_failure)
