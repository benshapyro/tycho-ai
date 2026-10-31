"""Closing the console window still shuts down safely (ADR 11).

When the window is closed, Windows ends the process as soon as the console's
handler returns: Python's own signal handling never gets to run, so a key held
mid-press would stay down. This handler asks the loop to stop, then waits until
shutdown has released every key (Windows allows about 5 seconds) before returning.
Ctrl+C is left to Python, which already cancels the loop cleanly.
"""

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes
from types import TracebackType
from typing import Self

CTRL_C, CTRL_BREAK, CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN = 0, 1, 2, 5, 6
_HANDLED = frozenset({CTRL_BREAK, CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN})
WAIT_SECONDS = 4.0

_HandlerType = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.SetConsoleCtrlHandler.argtypes = (_HandlerType, wintypes.BOOL)
_kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL


class CloseGuard:
    """While active, closing the window, logging off or Ctrl+Break stop Tycho safely."""

    def __init__(self, request_stop: Callable[[], None], wait: float = WAIT_SECONDS) -> None:
        self._request_stop = request_stop
        self._wait = wait
        self._finished = threading.Event()
        self._handler = _HandlerType(self.on_event)  # kept referenced while installed

    def __enter__(self) -> Self:
        _kernel32.SetConsoleCtrlHandler(self._handler, True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._finished.set()  # shutdown is done: let a waiting close go ahead
        _kernel32.SetConsoleCtrlHandler(self._handler, False)

    def on_event(self, kind: int) -> bool:
        """Runs on a thread Windows creates. True means "handled, don't end us yet"."""
        if kind not in _HANDLED:
            return False
        self._request_stop()
        self._finished.wait(self._wait)
        return True
