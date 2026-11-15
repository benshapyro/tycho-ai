"""Pressing a planned request's keys, one press at a time on the key thread, and saying
honestly what reached the game. Out of ``session.py`` (#399); what to press is
``planning``'s.

A cancelled request stops within about 10 ms with every key released, and its log
still says what it pressed.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor

from tycho.input import Press, Presser
from tycho.runtime.messages import Progress, Status, Trace

RECEIPT_STATUS: dict[str, Status] = {
    "cancelled": "cancelled",
    "no_focus": "no_focus",
    "failed": "failed",
}


TRIED = "Try it: nothing was sent to the game"
"""The detail of a request planned during setup's Try it, for the log."""


def _shown(press: Press) -> str:
    """ "Landing System (N)": the action in the game's words, and the player's key."""
    name = press.label or press.action
    return f"{name} ({press.key})" if press.key else name


async def press_all(
    presses: tuple[tuple[float, Press], ...],
    *,
    presser: Presser,
    keys: Executor,
    cancel: threading.Event,
    sleep: Callable[[float], Awaitable[None]],
    progress: Progress,
    trace: Trace,
    trying: bool = False,
) -> tuple[Status, str]:
    """Press each in turn, after its wait, adding what was pressed to ``progress``: how
    the request ended, and why if it didn't finish. ``trying``: setup's Try it (ADR 17),
    so nothing is sent and ``progress`` says what would have been pressed."""
    if trying:
        progress.would_press = [_shown(press) for _wait, press in presses]
        trace.mark("press")
        return "done", TRIED
    loop = asyncio.get_running_loop()

    def first_key_down() -> None:  # on the key thread, as the key goes down
        if progress.keydown_ms is None:
            progress.keydown_ms = trace.elapsed_ms()

    for wait, press in presses:
        if wait:
            await sleep(wait)
        running = loop.run_in_executor(keys, presser.press, press, cancel, first_key_down)
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
            return RECEIPT_STATUS.get(receipt.outcome, "failed"), receipt.detail
        progress.pressed.append(press.action)
    trace.mark("press")
    return "done", ""
