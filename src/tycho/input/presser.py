"""Carrying out presses safely: in order, cancellable, and always letting go.

Rules (AGENTS.md):
- Keys are only sent while the game has focus, so a command can never type
  into a chat window or another app. Focus is checked before every tap.
- Every key that went down is released, in reverse order, in a ``finally``,
  whatever happens: cancellation, lost focus, or an error from Windows.
- Waiting is done in small slices, so cancellation takes effect within ~10 ms.

This module presses keys; it doesn't decide whether an action is allowed.
That's the safety gate's job, upstream.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from tycho.input.keys import Code
from tycho.input.plan import Press

_SLICE_SECONDS = 0.01

type Outcome = Literal["done", "cancelled", "no_focus", "failed"]


class KeySender(Protocol):
    """Sends single key, button and wheel events to the operating system."""

    def down(self, code: Code) -> None: ...

    def up(self, code: Code) -> None: ...

    def wheel(self, notches: int) -> None: ...


class FocusCheck(Protocol):
    def game_has_focus(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class Receipt:
    """What actually happened, so Tycho can report it honestly."""

    action: str
    outcome: Outcome
    taps_done: int
    detail: str = ""

    @property
    def done(self) -> bool:
        return self.outcome == "done"


class Presser:
    def __init__(
        self,
        sender: KeySender,
        focus: FocusCheck,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sender = sender
        self._focus = focus
        self._sleep = sleep
        self._clock = clock

    def press(self, press: Press, cancel: threading.Event | None = None) -> Receipt:
        """Carry out ``press``. Blocks for the length of the press; call it off the event loop."""
        cancel = cancel or threading.Event()
        held: list[Code] = []
        taps = 0
        try:
            for tap in range(press.taps):
                if cancel.is_set():
                    return Receipt(press.action, "cancelled", taps)
                if not self._focus.game_has_focus():
                    return Receipt(press.action, "no_focus", taps, "Star Citizen isn't focused")
                if tap:  # between taps: a cancel or lost focus here stops the next tap
                    if not self._wait(press.gap_seconds, cancel):
                        return Receipt(press.action, "cancelled", taps)
                    if not self._focus.game_has_focus():
                        return Receipt(press.action, "no_focus", taps, "Star Citizen isn't focused")
                if not self._tap(press, held, cancel):
                    return Receipt(press.action, "cancelled", taps)
                taps += 1
            return Receipt(press.action, "done", taps)
        except OSError as e:
            return Receipt(press.action, "failed", taps, str(e))
        finally:
            self._release(held)

    def _tap(self, press: Press, held: list[Code], cancel: threading.Event) -> bool:
        """One press of the whole chord. Returns False if cancelled part way."""
        *modifiers, main = press.keys
        for code in modifiers:
            self._sender.down(code)
            held.append(code)
        if main.device == "wheel":
            self._sender.wheel(main.value)
        else:
            self._sender.down(main)
            held.append(main)
            if not self._wait(press.hold_seconds, cancel):
                return False
            self._release_one(held)
        while held:
            self._release_one(held)
        return True

    def _release_one(self, held: list[Code]) -> None:
        """Let go of the newest key. It stays in ``held`` until Windows has taken the
        key-up, so if that fails the ``finally`` still knows to release it."""
        self._sender.up(held[-1])
        held.pop()

    def _wait(self, seconds: float, cancel: threading.Event) -> bool:
        """Wait, waking early if cancelled. Returns False if cancelled."""
        deadline = self._clock() + seconds
        while (left := deadline - self._clock()) > 0:
            if cancel.is_set():
                return False
            self._sleep(min(_SLICE_SECONDS, left))
        return not cancel.is_set()

    def _release(self, held: list[Code]) -> None:
        """Let go of everything still down, newest first. A failed release is tried once
        more, and a key that still won't release doesn't stop the others."""
        while held:
            code = held.pop()
            for _ in range(2):
                try:
                    self._sender.up(code)
                    break
                except OSError:
                    continue
