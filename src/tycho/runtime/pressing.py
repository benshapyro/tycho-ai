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


async def press_all(
    presses: tuple[tuple[float, Press], ...],
    *,
    presser: Presser,
    keys: Executor,
    cancel: threading.Event,
    sleep: Callable[[float], Awaitable[None]],
    progress: Progress,
    trace: Trace,
) -> tuple[Status, str]:
    """Press each in turn, after its wait, adding what was pressed to ``progress``: how
    the request ended, and why if it didn't finish."""
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
