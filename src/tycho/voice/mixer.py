"""The game's own volume in Windows' mixer (#570): ``ducking.GameVolume`` on Windows.

Windows' Core Audio keeps an audio session for each program playing sound, with the
volume the mixer's per-app slider shows. This finds the game's sessions, by its
program's name (the pack's ``window``), on every active output device, and reads or
sets each one's volume: what a player does by hand in the mixer, nothing in the game
(ADR 3). Each is named by its session identifier, which Windows keeps the level under, so
it stays the same when the game restarts (#578).
COM through ``win32``, no new dependency.
"""

import ctypes
import logging
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from ctypes import wintypes

from tycho import win32
from tycho.input.windows import executable_of_process
from tycho.voice.ducking import ALL, Levels

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
_GET_SESSION_IDENTIFIER, _GET_PROCESS_ID = 12, 14  # IAudioSessionControl2
_SET_MASTER_VOLUME, _GET_MASTER_VOLUME = 3, 4  # ISimpleAudioVolume


class WindowsGameVolume:
    """The volume of every audio session of the program ``executable`` (StarCitizen.exe)."""

    def __init__(self, executable: str) -> None:
        self._executable = executable.lower()

    def get(self) -> Levels:
        levels: Levels = {}
        for name, volume in self._volumes():
            level = ctypes.c_float()
            win32.com_call(
                volume,
                _GET_MASTER_VOLUME,
                _H,
                [ctypes.POINTER(ctypes.c_float)],
                ctypes.byref(level),
            )
            # Two sessions under one name share the level Windows keeps: the louder.
            levels[name] = max(level.value, levels.get(name, 0.0))
        return levels

    def set(self, levels: Mapping[str, float]) -> bool:
        found = False
        for name, volume in self._volumes():
            level = levels.get(name, levels.get(ALL))
            if level is None:
                continue
            level = min(max(level, 0.0), 1.0)
            win32.com_call(volume, _SET_MASTER_VOLUME, _H, [ctypes.c_float, _P], level, None)
            found = True
        return found

    def _volumes(self) -> Iterator[tuple[str, int]]:
        """Each of the game's sessions' name and ``ISimpleAudioVolume``, released after
        use."""
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

    def _sessions(self, on_device: _Call) -> Iterator[tuple[str, int]]:
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
                    try:
                        control = _out(listing, _GET_SESSION, [ctypes.c_int], i)
                        with _owned(control) as session:
                            name = self._the_games(session)
                            if name is None:
                                continue
                            volume = _query(session, _ISIMPLE_AUDIO_VOLUME)
                    except OSError as e:
                        # One session failing (a headset unplugged mid-walk) skips only
                        # that session, not the rest (#579).
                        log.debug("Skipped an audio session: %s", e)
                        continue
                    with _owned(volume):
                        yield name, volume

    def _the_games(self, session: _Call) -> str | None:
        """The session's identifier if it's the game's, else None."""
        control2 = _query(session, _IAUDIO_SESSION_CONTROL2)
        with _owned(control2) as asking:
            pid = wintypes.DWORD()
            # Unsigned, so ``com_call`` doesn't raise on it (``win32.HRESULT`` is
            # ``c_long``): a session of several processes answers with a warning, which
            # is positive. A failure is negative as a signed number.
            hr = asking(
                _GET_PROCESS_ID, ctypes.c_ulong, [ctypes.POINTER(wintypes.DWORD)], ctypes.byref(pid)
            )
            if not isinstance(hr, int) or hr & 0x80000000 or not pid.value:
                return None
            if executable_of_process(pid.value).lower() != self._executable:
                return None
            text = _P()  # a string Windows made, freed here
            asking(_GET_SESSION_IDENTIFIER, _H, [_PP], ctypes.byref(text))
            try:
                return ctypes.wstring_at(text.value) if text.value else self._executable
            finally:
                win32.ole32().CoTaskMemFree(text)


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
