"""The push-to-talk devices: what Tycho listens to, and a binding's name for people.

Out of ``app.py`` (#398), so the desktop doesn't import the app to use them.
"""

import logging
from collections.abc import Iterable

from tycho.ptt.binding import Binding, DeviceKind, every_binding
from tycho.ptt.monitor import InputSource, PushToTalk

log = logging.getLogger(__name__)


def describe(binding: Binding) -> str:
    """A button's name for people. A stick's is its own name once Tycho has seen it
    plugged in (#623), so with two sticks the player can tell which: "VKBsim Gladiator
    EVO R button 12", and "(2nd)" after it when two have the same name."""
    from tycho.ptt import windows as win

    if binding.kind is DeviceKind.JOYSTICK:
        return f"{stick_name(binding)} button {binding.code + 1}"
    return win.describe(binding)


def stick_name(binding: Binding) -> str:
    """The stick a button is on, by its name, or "joystick" when it isn't plugged in."""
    from tycho.ptt import sticks

    device = binding.device or ""
    found = sticks.name(device)
    if found is None:
        return "joystick"
    _, mark, number = device.partition("#")
    twins = sticks.doubled().get(sticks.guid(device), [])
    if twins.count(found) > 1:
        return f"{found} ({_ordinal(int(number) if mark and number.isdigit() else 1)})"
    return found


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


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


def rebind(ptt: PushToTalk, first: str | None, second: str | None) -> bool:
    """The buttons as saved, chosen again in the app while Tycho runs (#646): it listens
    to them from the next poll, a stick's opened only if one needs it. False with no
    first button: nothing to listen to, so the old ones stay."""
    bindings = every_binding(first, second)
    if not bindings:
        return False
    ptt.rebind(bindings, input_sources(*bindings))
    return True


def look_at_sticks() -> None:
    """Open the sticks once and let them go, so Tycho knows their names and any pair
    that can swap (#623): for ``tycho check``, where nothing else has opened them. Only
    where no other thread has SDL open. A stick library that won't start is no problem
    here: the names are only nicer."""
    try:
        from tycho.ptt.joystick import JoystickSource

        JoystickSource().close()
    except Exception:  # SDL missing or failing: the plain names stay
        log.debug("Couldn't look at the sticks", exc_info=True)


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
