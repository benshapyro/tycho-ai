"""The game's own volume in Windows' mixer (#570): ``ducking.GameVolume`` on Windows.

Windows' Core Audio keeps an audio session for each program playing sound, with the
volume the mixer's per-app slider shows. This finds the game's sessions, by its
program's name (the pack's ``window``), on every active output device, and reads or
sets their volume: what a player does by hand in the mixer, nothing in the game (ADR 3).
COM through ``win32``, no new dependency.
"""

import ctypes
import logging
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from ctypes import wintypes

from tycho import win32
from tycho.input.windows import executable_of_process

log = logging.getLogger(__name__)

_ENUMERATOR = win32.guid("BCDE0395-E52F-467C-8E3D-C4579291692E")  # MMDeviceEnumerator
_IMM_DEVICE_ENUMERATOR = win32.guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IAUDIO_SESSION_MANAGER2 = win32.guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
_IAUDIO_SESSION_CONTROL2 = win32.guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
_ISIMPLE_AUDIO_VOLUME = win32.guid("87CE5498-68D6-44E5-9215-6DA47EF883D8")
_CLSCTX_ALL = 0x17
_COINIT_MULTITHREADED = 0x0
_RENDER, _ACTIVE = 0, 0x1
_P, _H = ctypes.c_void_p, win32.HRESULT
_PP = ctypes.POINTER(ctypes.c_void_p)
# Each method's place in its interface's vtable (after IUnknown's three).
_QUERY_INTERFACE, _RELEASE = 0, 2
_ENUM_AUDIO_ENDPOINTS = 3  # IMMDeviceEnumerator
_COLLECTION_COUNT, _COLLECTION_ITEM = 3, 4  # IMMDeviceCollection
_ACTIVATE = 3  # IMMDevice
_GET_SESSION_ENUMERATOR = 5  # IAudioSessionManager2
_SESSION_COUNT, _GET_SESSION = 3, 4  # IAudioSessionEnumerator
_GET_PROCESS_ID = 14  # IAudioSessionControl2
_SET_MASTER_VOLUME, _GET_MASTER_VOLUME = 3, 4  # ISimpleAudioVolume


class WindowsGameVolume:
    """The volume of every audio session of the program ``executable`` (StarCitizen.exe)."""

    def __init__(self, executable: str) -> None:
        self._executable = executable.lower()

    def get(self) -> float | None:
        levels: list[float] = []
        for volume in self._volumes():
            level = ctypes.c_float()
            win32.com_call(
                volume,
                _GET_MASTER_VOLUME,
                _H,
                [ctypes.POINTER(ctypes.c_float)],
                ctypes.byref(level),
            )
            levels.append(level.value)
        return max(levels) if levels else None

    def set(self, level: float) -> bool:
        level = min(max(level, 0.0), 1.0)
        found = False
        for volume in self._volumes():
            win32.com_call(volume, _SET_MASTER_VOLUME, _H, [ctypes.c_float, _P], level, None)
            found = True
        return found

    def _volumes(self) -> Iterator[int]:
        """Each of the game's sessions' ``ISimpleAudioVolume``, released after use."""
        ole32 = win32.ole32()
        ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)  # once per thread; repeats are fine
        enumerator = _P()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_ENUMERATOR),
            None,
            _CLSCTX_ALL,
            ctypes.byref(_IMM_DEVICE_ENUMERATOR),
            ctypes.byref(enumerator),
        )
        if hr < 0 or not enumerator.value:
            raise OSError(f"no audio devices to ask ({hr & 0xFFFFFFFF:#010x})")
        with _owned(enumerator.value) as devices_of:
            collection = _out(
                devices_of, _ENUM_AUDIO_ENDPOINTS, [wintypes.UINT, wintypes.DWORD], _RENDER, _ACTIVE
            )
            with _owned(collection) as items:
                count = wintypes.UINT()
                items(_COLLECTION_COUNT, _H, [ctypes.POINTER(wintypes.UINT)], ctypes.byref(count))
                for i in range(count.value):
                    device = _out(items, _COLLECTION_ITEM, [wintypes.UINT], i)
                    with _owned(device) as on_device:
                        yield from self._sessions(on_device)

    def _sessions(self, on_device: _Call) -> Iterator[int]:
        manager = _out(
            on_device,
            _ACTIVATE,
            [ctypes.POINTER(win32.GUID), wintypes.DWORD, _P],
            ctypes.byref(_IAUDIO_SESSION_MANAGER2),
            _CLSCTX_ALL,
            None,
        )
        with _owned(manager) as managing:
            sessions = _out(managing, _GET_SESSION_ENUMERATOR, [])
            with _owned(sessions) as listing:
                count = ctypes.c_int()
                listing(_SESSION_COUNT, _H, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(count))
                for i in range(count.value):
                    control = _out(listing, _GET_SESSION, [ctypes.c_int], i)
                    with _owned(control) as session:
                        if self._is_the_games(session):
                            volume = _query(session, _ISIMPLE_AUDIO_VOLUME)
                            with _owned(volume):
                                yield volume

    def _is_the_games(self, session: _Call) -> bool:
        control2 = _query(session, _IAUDIO_SESSION_CONTROL2)
        with _owned(control2) as asking:
            pid = wintypes.DWORD()
            # Not HRESULT-checked: a session of several processes answers with a warning.
            asking(
                _GET_PROCESS_ID, ctypes.c_long, [ctypes.POINTER(wintypes.DWORD)], ctypes.byref(pid)
            )
        return bool(pid.value) and executable_of_process(pid.value).lower() == self._executable


type _Call = Callable[..., object]
"""A COM object's method caller: (index, restype, argtypes, *args)."""


@contextmanager
def _owned(obj: int) -> Generator[_Call]:
    """A COM object's methods, and its release once done."""

    def call(index: int, restype: object, argtypes: list[object], *args: object) -> object:
        return win32.com_call(obj, index, restype, argtypes, *args)

    try:
        yield call
    finally:
        win32.com_call(obj, _RELEASE, ctypes.c_ulong, [])


def _out(call: _Call, index: int, argtypes: list[object], *args: object) -> int:
    """Call a method whose last argument receives a new COM object, and return it."""
    made = _P()
    call(index, _H, [*argtypes, _PP], *args, ctypes.byref(made))
    if not made.value:
        raise OSError(f"COM call {index} gave nothing")
    return made.value


def _query(call: _Call, interface: win32.GUID) -> int:
    return _out(call, _QUERY_INTERFACE, [ctypes.POINTER(win32.GUID)], ctypes.byref(interface))
