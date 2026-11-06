"""Only one Tycho at a time, so two copies never fight over the keyboard (ADR 11).

Starting Tycho again while it runs (the Start menu, say) asks the running one to show
its window (#146): a named Windows event, set by the second start and waited on by
the first. Nothing listens on the network.
"""

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes import wintypes
from types import TracebackType
from typing import Self

_ERROR_ALREADY_EXISTS = 183
DEFAULT_NAME = r"Local\tycho-ai"
"""Per Windows session: another user logged in on the same PC can run their own Tycho."""
OPEN_NAME = r"Local\tycho-ai-open"
"""Set by a second start: "show your window"."""
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF

log = logging.getLogger(__name__)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.WaitForMultipleObjects.argtypes = (
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.BOOL,
    wintypes.DWORD,
)
_kernel32.WaitForMultipleObjects.restype = wintypes.DWORD


class AlreadyRunning(Exception):
    """Another Tycho is already running."""


class SingleInstance:
    """Holds a named mutex for as long as the ``with`` block runs.

    Windows releases it if the process dies, so a crash never locks Tycho out.
    """

    def __init__(self, name: str = DEFAULT_NAME) -> None:
        self.name = name
        self._handle: int | None = None

    def __enter__(self) -> Self:
        handle = _kernel32.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == _ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            raise AlreadyRunning("Tycho is already running. Close the other one first.")
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


def ask_to_open(name: str = OPEN_NAME) -> bool:
    """Ask the running Tycho to show its window. False if it isn't listening for that
    (a terminal-only Tycho, or one still starting)."""
    handle = _kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(_kernel32.SetEvent(handle))
    finally:
        _kernel32.CloseHandle(handle)


class OpenRequests:
    """Waits, on a thread of its own, for a second start to ask for the window, and
    calls ``target`` each time. ``target`` can change while it runs: the first run's
    window, then the tray's."""

    def __init__(self, name: str = OPEN_NAME) -> None:
        self.name = name
        self.target: Callable[[], None] = lambda: None
        self._event: int | None = None
        self._stop: int | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        event = _kernel32.CreateEventW(None, False, False, self.name)  # auto-reset
        stop = _kernel32.CreateEventW(None, True, False, None)
        if not event or not stop:
            for handle in (event, stop):
                if handle:
                    _kernel32.CloseHandle(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self._event, self._stop = event, stop
        self._thread = threading.Thread(target=self._wait, name="tycho-open", daemon=True)
        self._thread.start()
        return self

    def _wait(self) -> None:
        if self._event is None or self._stop is None:
            return
        handles = (wintypes.HANDLE * 2)(self._event, self._stop)
        while True:
            which = _kernel32.WaitForMultipleObjects(2, handles, False, _INFINITE)
            if which != _WAIT_OBJECT_0:
                return  # stopped, or the wait failed
            try:
                self.target()
            except Exception:
                log.exception("Couldn't show Tycho's window for a second start")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stop is not None:
            _kernel32.SetEvent(self._stop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        for handle in (self._event, self._stop):
            if handle:
                _kernel32.CloseHandle(handle)
        self._event = self._stop = None
