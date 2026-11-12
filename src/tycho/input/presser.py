"""Carrying out presses safely: in order, cancellable, and always letting go.

Rules (AGENTS.md):
- Keys are only sent while the game has focus, so a command can never type
  into a chat window or another app. Focus is checked before every tap.
- Every key that went down is released, in reverse order, in a ``finally``,
  whatever happens: cancellation, lost focus, or an error from Windows.
- Waiting is done in small slices, so cancellation takes effect within ~10 ms,
  except that a hold past the game's tap window is never cut short inside it
  (``Press.min_hold_seconds``, at most 0.3 s), so a cancel can't turn it into a tap of
  another action on the same key.

This module presses keys; it doesn't decide whether an action is allowed.
That's the safety gate's job, upstream.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from tycho.input.keys import Code
from tycho.input.plan import Press

log = logging.getLogger(__name__)

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
    interrupted: bool = False
    """Stopped part way through a tap, after its key went down: the game may have acted."""

    @property
    def sent(self) -> bool:
        """Whether any of its keys reached the game."""
        return self.taps_done > 0 or self.interrupted

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
        game: str = "Star Citizen",
    ) -> None:
        """``game`` is the game's name, for "isn't focused" (the game pack's)."""
        self._sender = sender
        self._focus = focus
        self._unfocused = f"{game} isn't focused"
        self._sleep = sleep
        self._clock = clock

    def press(
        self,
        press: Press,
        cancel: threading.Event | None = None,
        on_down: Callable[[], None] | None = None,
    ) -> Receipt:
        """Carry out ``press``. Blocks for the length of the press; call it off the event loop.
        ``on_down`` is called (on this thread) the moment the first key the game acts on
        goes down: the time that matters to the player, not when the tap ends."""
        cancel = cancel or threading.Event()
        held: list[Code] = []
        taps = 0
        try:
            for tap in range(press.taps):
                if cancel.is_set():
                    return Receipt(press.action, "cancelled", taps)
                if not self._focus.game_has_focus():
                    return Receipt(press.action, "no_focus", taps, self._unfocused)
                if tap:  # between taps: a cancel or lost focus here stops the next tap
                    if not self._wait(press.gap_seconds, cancel):
                        return Receipt(press.action, "cancelled", taps)
                    if not self._focus.game_has_focus():
                        return Receipt(press.action, "no_focus", taps, self._unfocused)
                if not self._tap(press, held, cancel, on_down if tap == 0 else None):
                    return Receipt(press.action, "cancelled", taps, interrupted=press.main in held)
                taps += 1
            return Receipt(press.action, "done", taps)
        except OSError as e:
            return Receipt(press.action, "failed", taps, str(e))
        finally:
            self._release(held, press.action)

    def _tap(
        self,
        press: Press,
        held: list[Code],
        cancel: threading.Event,
        on_down: Callable[[], None] | None = None,
    ) -> bool:
        """One press of the whole chord. Returns False if cancelled part way."""
        *modifiers, main = press.keys
        for code in modifiers:
            self._sender.down(code)
            held.append(code)
        if main.device == "wheel":
            self._sender.wheel(main.value)
            if on_down is not None:
                on_down()
        else:
            self._sender.down(main)
            held.append(main)
            if on_down is not None:
                on_down()
            down_at = self._clock()
            if not self._wait(press.hold_seconds, cancel):
                self._hold_on(down_at + press.min_hold_seconds)
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

    def _hold_on(self, until: float) -> None:
        """Keep holding until ``until`` (on this clock), whatever cancels. Bounded by
        ``Press.min_hold_seconds``."""
        while (left := until - self._clock()) > 0:
            self._sleep(min(_SLICE_SECONDS, left))

    def _release(self, held: list[Code], action: str) -> None:
        """Let go of everything still down, newest first. A failed release is tried once
        more, and a key that still won't release doesn't stop the others, but is
        reported: the game may think it's still held."""
        while held:
            code = held.pop()
            for attempt in range(2):
                try:
                    self._sender.up(code)
                    break
                except OSError as e:
                    if attempt == 1:
                        log.error(
                            "Windows wouldn't let go of a key (%s) after %s: %s. If the game "
                            "acts as if it's held, press that key once yourself.",
                            code,
                            action,
                            e,
                        )
