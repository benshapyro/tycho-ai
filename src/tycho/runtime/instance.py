"""Only one Tycho at a time, so two copies never fight over the keyboard (ADR 11)."""

import ctypes
from ctypes import wintypes
from types import TracebackType
from typing import Self

_ERROR_ALREADY_EXISTS = 183
DEFAULT_NAME = r"Local\tycho-ai"
"""Per Windows session: another user logged in on the same PC can run their own Tycho."""

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL


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
