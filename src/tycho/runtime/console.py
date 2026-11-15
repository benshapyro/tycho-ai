"""Closing the console window still shuts down safely (ADR 11).

When the window is closed, Windows ends the process as soon as the console's
handler returns: Python's own signal handling never gets to run, so a key held
mid-press would stay down. This handler asks the loop to stop, then waits until
shutdown has released every key (Windows allows about 5 seconds) before returning.
Ctrl+C is left to Python, which already cancels the loop cleanly.
"""

import threading
from collections.abc import Callable
from types import TracebackType
from typing import Self

from tycho import win32

CTRL_C, CTRL_BREAK, CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN = 0, 1, 2, 5, 6
_HANDLED = frozenset({CTRL_BREAK, CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN})
WAIT_SECONDS = 4.0


class CloseGuard:
    """While active, closing the window, logging off or Ctrl+Break stop Tycho safely."""

    def __init__(
        self,
        request_stop: Callable[[], None],
        wait: float = WAIT_SECONDS,
        ending: Callable[[], None] = lambda: None,
    ) -> None:
        """``ending`` is called last, just before Windows ends the process: nothing
        Python registered to run at exit will (#428)."""
        self._request_stop = request_stop
        self._ending = ending
        self._wait = wait
        self._finished = threading.Event()
        self._handler = win32.HANDLER_ROUTINE(self.on_event)  # kept referenced while installed

    def __enter__(self) -> Self:
        win32.kernel32().SetConsoleCtrlHandler(self._handler, True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._finished.set()  # shutdown is done: let a waiting close go ahead
        win32.kernel32().SetConsoleCtrlHandler(self._handler, False)

    def on_event(self, kind: int) -> bool:
        """Runs on a thread Windows creates. True means "handled, don't end us yet"."""
        if kind not in _HANDLED:
            return False
        self._request_stop()
        self._finished.wait(self._wait)
        self._ending()
        return True


class CtrlCGuard:
    """While active, Ctrl+C also calls ``on_ctrl_c`` at once, on a thread Windows
    creates. For when the main thread is busy in a window's message loop, where Python
    would only raise KeyboardInterrupt after the window closed. Python still gets the
    Ctrl+C too (the handler returns False)."""

    def __init__(self, on_ctrl_c: Callable[[], None]) -> None:
        self._on_ctrl_c = on_ctrl_c
        self._handler = win32.HANDLER_ROUTINE(self.on_event)

    def __enter__(self) -> Self:
        win32.kernel32().SetConsoleCtrlHandler(self._handler, True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        win32.kernel32().SetConsoleCtrlHandler(self._handler, False)

    def on_event(self, kind: int) -> bool:
        if kind == CTRL_C:
            self._on_ctrl_c()
        return False
