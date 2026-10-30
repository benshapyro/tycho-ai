"""Joystick buttons, read through SDL (pygame-ce).

SDL keeps reading joysticks while another window, such as the game, has focus.
Create and poll a ``JoystickSource`` from one thread only.
"""

import os

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

from ultimo.ptt.binding import Binding, DeviceKind


class JoystickSource:
    """All buttons on all connected joysticks, including hot-plugged ones.

    A joystick is identified by its SDL GUID, which is stable across reconnects.
    Identical sticks share a GUID, so duplicates get ``#2``, ``#3`` in connection
    order. Configuring them with distinct USB IDs (VKB's tool can) avoids that.
    """

    def __init__(self) -> None:
        pygame.display.init()
        pygame.joystick.init()
        self._sticks: dict[str, pygame.joystick.JoystickType] = {}
        self.names: dict[str, str] = {}
        self._refresh()

    def pressed(self) -> frozenset[Binding]:
        self._pump()
        return frozenset(
            Binding(DeviceKind.JOYSTICK, button, device)
            for device, stick in self._sticks.items()
            for button in range(stick.get_numbuttons())
            if stick.get_button(button)
        )

    def is_pressed(self, binding: Binding) -> bool:
        if binding.kind is not DeviceKind.JOYSTICK or binding.device is None:
            return False
        self._pump()
        stick = self._sticks.get(binding.device)
        return (
            stick is not None
            and binding.code < stick.get_numbuttons()
            and stick.get_button(binding.code)
        )

    def describe(self, binding: Binding) -> str:
        name = self.names.get(binding.device or "", "Joystick")
        return f"{name} button {binding.code + 1}"

    def _pump(self) -> None:
        changed = pygame.event.get([pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED])
        pygame.event.clear()
        if changed:
            self._refresh()

    def _refresh(self) -> None:
        self._sticks.clear()
        self.names.clear()
        seen: dict[str, int] = {}
        for index in range(pygame.joystick.get_count()):
            stick = pygame.joystick.Joystick(index)
            guid = stick.get_guid()
            seen[guid] = seen.get(guid, 0) + 1
            device = guid if seen[guid] == 1 else f"{guid}#{seen[guid]}"
            self._sticks[device] = stick
            self.names[device] = stick.get_name()
