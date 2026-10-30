"""Push-to-talk bindings: which physical button triggers listening."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class DeviceKind(StrEnum):
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    JOYSTICK = "joystick"


@dataclass(frozen=True, slots=True)
class Binding:
    """One physical button.

    Keyboard and mouse codes are Windows virtual-key codes. Joystick codes are
    button indexes, and ``device`` identifies the joystick (see ``JoystickSource``).
    """

    kind: DeviceKind
    code: int
    device: str | None = None

    def __post_init__(self) -> None:
        if self.code < 0:
            raise ValueError(f"button code must be non-negative, got {self.code}")
        if (self.kind is DeviceKind.JOYSTICK) != (self.device is not None):
            raise ValueError("joystick bindings need a device; other bindings must not have one")

    def __str__(self) -> str:
        if self.kind is DeviceKind.JOYSTICK:
            return f"joystick:{self.device}:{self.code}"
        return f"{self.kind}:{self.code:#04x}"

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse the form produced by ``str()``, e.g. ``mouse:0x05`` or ``joystick:<id>:3``."""
        kind_text, _, rest = text.strip().partition(":")
        try:
            kind = DeviceKind(kind_text.lower())
        except ValueError:
            raise ValueError(f"unknown device kind in binding {text!r}") from None
        try:
            if kind is DeviceKind.JOYSTICK:
                device, sep, code = rest.rpartition(":")
                if not sep or not device:
                    raise ValueError
                return cls(kind, int(code), device)
            return cls(kind, int(rest, 0))
        except ValueError:
            raise ValueError(f"malformed binding {text!r}") from None
