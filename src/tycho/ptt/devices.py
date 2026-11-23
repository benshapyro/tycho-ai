"""The push-to-talk devices: what Tycho listens to, and a binding's name for people.

Out of ``app.py`` (#398), so the desktop doesn't import the app to use them.
"""

from collections.abc import Iterable

from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.monitor import InputSource


def describe(binding: Binding) -> str:
    from tycho.ptt import windows as win

    if binding.kind is DeviceKind.JOYSTICK:
        return f"joystick button {binding.code + 1}"
    return win.describe(binding)


def describe_all(bindings: Iterable[Binding]) -> str:
    """Every push-to-talk button by its name: "Mouse side (forward) or joystick button 3"."""
    return " or ".join(describe(binding) for binding in bindings)


def input_sources(binding: Binding | None, *more: Binding) -> list[InputSource]:
    """Keyboard and mouse always; joysticks when a binding needs them, or with None (to
    capture one)."""
    from tycho.ptt.windows import KeyboardMouseSource

    sources: list[InputSource] = [KeyboardMouseSource()]
    if binding is None or any(b.kind is DeviceKind.JOYSTICK for b in (binding, *more)):
        sources.append(PollThreadJoysticks())
    return sources


class PollThreadJoysticks:
    """Joysticks, opened on first use: SDL must be set up on the thread that polls it."""

    def __init__(self) -> None:
        self._source: InputSource | None = None

    def _get(self) -> InputSource:
        if self._source is None:
            from tycho.ptt.joystick import JoystickSource

            self._source = JoystickSource()
        return self._source

    def pressed(self) -> frozenset[Binding]:
        return self._get().pressed()

    def is_pressed(self, binding: Binding) -> bool:
        return binding.kind is DeviceKind.JOYSTICK and self._get().is_pressed(binding)

    def close(self) -> None:
        """Let SDL go, on the thread that set it up (a capture from the app window)."""
        source, self._source = self._source, None
        close = getattr(source, "close", None)
        if close is not None:
            close()
