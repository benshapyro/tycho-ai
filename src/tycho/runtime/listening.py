"""Push-to-talk and the end of a clip: polling the button on its own thread, and the
short tail kept after release. Out of ``session.py`` (#399)."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from tycho.audio.source import AudioSource
from tycho.ptt.monitor import Edge

log = logging.getLogger(__name__)

TAIL_SECONDS = 0.15
"""The most audio kept after release: people let go as they finish the last word, and
the microphone delivers audio in blocks. The benchmark recordings were made this way."""
QUIET_TAIL_SECONDS = 0.06
"""The tail ends early once the clip has been this quiet: the player has finished.
On the recorded test set this ends most tails at release, saving about 0.12 s (#58)."""
TAIL_CHECK_SECONDS = 0.01
POLL_RETRY_SECONDS = 1.0
"""How long to wait before reading push-to-talk again after it failed."""


def poll(
    read: Callable[[], Edge | None],
    interval: float,
    halt: threading.Event,
    loop: asyncio.AbstractEventLoop,
    handle: Callable[[Edge], None],
) -> None:
    """Read push-to-talk every ``interval`` until ``halt``, handing each press and
    release to ``handle`` on the event loop. A failure is logged and retried."""
    failing = ""
    while not halt.is_set():
        # Not halt.wait(interval): on Windows that rounds up to the 15.6 ms timer
        # tick, adding about 8 ms to every press and release. time.sleep doesn't.
        time.sleep(interval)
        try:
            edge = read()
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
                loop.call_soon_threadsafe(handle, edge)
            except RuntimeError:  # the loop closed underneath us
                return


async def tail(audio: AudioSource, seconds: float) -> None:
    """Wait up to ``seconds``, ending as soon as the clip goes quiet."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while audio.quiet_seconds() < QUIET_TAIL_SECONDS:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(TAIL_CHECK_SECONDS, remaining))
