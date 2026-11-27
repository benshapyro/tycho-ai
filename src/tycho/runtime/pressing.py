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
from tycho.keybinds import readable
from tycho.runtime.messages import Progress, Status, Trace

RECEIPT_STATUS: dict[str, Status] = {
    "cancelled": "cancelled",
    "no_focus": "no_focus",
    "modifier_held": "modifier_held",
    "failed": "failed",
}


TRIED = "Try it: nothing was sent to the game"
"""The detail of a request planned during setup's Try it, for the log."""
TRYING_IN_GAME = "Try it is still on, so nothing was sent. Turn it off in Tycho's window."
"""Try it was on, but the game was in front: the window didn't hear the switch (#467).
Not done, so the player doesn't think it worked."""
SAID_DURING_TRY_IT = "That was said during Try it, so nothing was sent."
"""Said with Try it on, which was turned off (the switch to the game) before the press
was planned: the game is in front, but Try it is already off, so "still on" would be
wrong (#507). Not done either."""


def _shown(press: Press) -> str:
    """ "Landing System (N)": the action in the game's words, and the player's key."""
    name = press.label or readable(press.action)
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
    still_trying: Callable[[], bool] = lambda: True,
) -> tuple[Status, str]:
    """Press each in turn, after its wait, adding what was pressed to ``progress``: how
    the request ended, and why if it didn't finish. ``trying``: setup's Try it (ADR 17),
    so nothing is sent and ``progress`` says what would have been pressed; with the
    game in front it isn't done either (#467). ``still_trying``: whether Try it is on now,
    which picks what that refusal says (#507)."""
    loop = asyncio.get_running_loop()
    if trying:
        in_front = await loop.run_in_executor(keys, presser.game_in_front)
        trace.mark("press")
        if in_front:  # a second guard: the player is playing, and nothing was pressed
            return "refused", TRYING_IN_GAME if still_trying() else SAID_DURING_TRY_IT
        progress.would_press = [_shown(press) for _wait, press in presses]
        return "done", TRIED

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
            if receipt.outcome == "modifier_held":
                # Said, in place of any line about pressing it (#645): "Let go of Ctrl
                # and say it again."
                progress.say, progress.toggled = receipt.detail, False
            trace.mark("press")
            return RECEIPT_STATUS.get(receipt.outcome, "failed"), receipt.detail
        progress.pressed.append(press.action)
    trace.mark("press")
    return "done", ""
