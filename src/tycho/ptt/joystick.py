"""Joystick buttons, read through SDL (PySDL2, with SDL from pysdl2-dll).

SDL keeps reading joysticks while another window, such as the game, has focus.
Create and poll a ``JoystickSource`` from one thread only.

PySDL2 is public domain or zlib, and SDL itself zlib; it replaced pygame-ce,
which is LGPL (#56). Only ``SDL2.dll`` is needed: an installed build should leave
out the rest of pysdl2-dll's DLLs, some of which are LGPL.
"""

# PySDL2 binds SDL through ctypes and ships no type information.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false, reportAttributeAccessIssue=false

import ctypes
import logging
import warnings
from typing import Any

from tycho.ptt.binding import Binding, DeviceKind

with warnings.catch_warnings():  # "Using SDL2 binaries from pysdl2-dll": expected, not news
    warnings.simplefilter("ignore", UserWarning)
    import sdl2

log = logging.getLogger(__name__)

_GUID_CHARS = 33
"""An SDL GUID as text: 32 hex digits and the terminator."""
_PLUGGED = (sdl2.SDL_JOYDEVICEADDED, sdl2.SDL_JOYDEVICEREMOVED)


class JoystickError(RuntimeError):
    """SDL couldn't start its joystick support."""


class JoystickSource:
    """All buttons on all connected joysticks, including hot-plugged ones.

    A joystick is identified by its SDL GUID, which is stable across reconnects.
    Identical sticks share a GUID, so duplicates get ``#2``, ``#3`` in connection
    order. Configuring them with distinct USB IDs (VKB's tool can) avoids that.
    """

    def __init__(self) -> None:
        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
        if sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK) != 0:
            raise JoystickError(f"couldn't start joysticks: {_sdl_error()}")
        self._sticks: dict[str, Any] = {}
        self.names: dict[str, str] = {}
        self._refresh()

    def pressed(self) -> frozenset[Binding]:
        self._pump()
        return frozenset(
            Binding(DeviceKind.JOYSTICK, button, device)
            for device, stick in self._sticks.items()
            for button in range(sdl2.SDL_JoystickNumButtons(stick))
            if sdl2.SDL_JoystickGetButton(stick, button)
        )

    def is_pressed(self, binding: Binding) -> bool:
        if binding.kind is not DeviceKind.JOYSTICK or binding.device is None:
            return False
        self._pump()
        stick = self._sticks.get(binding.device)
        return (
            stick is not None
            and binding.code < sdl2.SDL_JoystickNumButtons(stick)
            and bool(sdl2.SDL_JoystickGetButton(stick, binding.code))
        )

    def describe(self, binding: Binding) -> str:
        name = self.names.get(binding.device or "", "Joystick")
        return f"{name} button {binding.code + 1}"

    def close(self) -> None:
        for stick in self._sticks.values():
            sdl2.SDL_JoystickClose(stick)
        self._sticks.clear()
        sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_JOYSTICK)

    def _pump(self) -> None:
        """Read SDL's events: button states update, and plugging in or out is noticed."""
        changed = False
        event = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(event)):
            changed = changed or event.type in _PLUGGED
        if changed:
            self._refresh()

    def _refresh(self) -> None:
        for stick in self._sticks.values():
            sdl2.SDL_JoystickClose(stick)
        self._sticks.clear()
        self.names.clear()
        seen: dict[str, int] = {}
        for index in range(sdl2.SDL_NumJoysticks()):
            stick = sdl2.SDL_JoystickOpen(index)
            if not stick:
                log.debug("Couldn't open joystick %d: %s", index, _sdl_error())
                continue
            guid = _guid(stick)
            seen[guid] = seen.get(guid, 0) + 1
            device = guid if seen[guid] == 1 else f"{guid}#{seen[guid]}"
            self._sticks[device] = stick
            name = sdl2.SDL_JoystickName(stick)
            self.names[device] = name.decode("utf-8", "replace") if name else "Joystick"


def _guid(stick: Any) -> str:
    """The stick's GUID as SDL writes it: the same text pygame gave, so saved bindings hold."""
    text = ctypes.create_string_buffer(_GUID_CHARS)
    sdl2.SDL_JoystickGetGUIDString(sdl2.SDL_JoystickGetGUID(stick), text, _GUID_CHARS)
    return text.value.decode("ascii")


def _sdl_error() -> str:
    error = sdl2.SDL_GetError()
    return error.decode("utf-8", "replace") if error else "unknown error"
