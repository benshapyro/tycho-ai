"""Is the push-to-talk button also bound in game? (ADR 11)

Tycho sees the press but can't stop the game seeing it too, so holding a
button bound to, say, "Landing gear" would toggle the gear while you talk.
Only bindings without modifiers count: a press of Mouse 5 alone doesn't
trigger an action bound to Ctrl + Mouse 5. Joystick bindings aren't read
from the game yet, so joystick push-to-talk isn't checked.
"""

from collections.abc import Callable

from tycho.game.keybinds import Action, Keybinds
from tycho.input.keys import Code, UnsupportedKey, code_for
from tycho.ptt.binding import Binding, DeviceKind

_MOUSE_BUTTONS = {0x01: 1, 0x02: 2, 0x04: 3, 0x05: 4, 0x06: 5}
"""Windows virtual-key codes for mouse buttons, to the game's mouse1..mouse5."""


def game_code(binding: Binding, scan_code: Callable[[int], tuple[int, bool]]) -> Code | None:
    """The push-to-talk button as the game would see it, or None if it can't be compared."""
    if binding.kind is DeviceKind.MOUSE:
        button = _MOUSE_BUTTONS.get(binding.code)
        return Code("mouse", button) if button else None
    if binding.kind is DeviceKind.KEYBOARD:
        scan, extended = scan_code(binding.code)
        return Code("key", scan, extended) if scan else None
    return None


def conflicts(code: Code, keybinds: Keybinds) -> list[Action]:
    """Game actions that a press of ``code`` on its own would trigger."""
    found: list[Action] = []
    for action in keybinds:
        for key in action.inputs:
            if key.modifiers or not key.pressable:
                continue
            try:
                if code_for(key.key) == code:
                    found.append(action)
                    break
            except UnsupportedKey:
                continue
    return found
