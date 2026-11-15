"""Is the push-to-talk button also bound in game? (ADR 11)

Tycho sees the press but can't stop the game seeing it too, so holding a
button bound to, say, "Landing gear" would toggle the gear while you talk.
Only bindings without modifiers count: a press of Mouse 5 alone doesn't
trigger an action bound to Ctrl + Mouse 5. Joystick bindings aren't read
from the game yet, so joystick push-to-talk isn't checked.
"""

from collections.abc import Callable

from tycho.findings import TYCHO
from tycho.input.keys import Code, UnsupportedKey, code_for
from tycho.keybinds import Action, Keybinds
from tycho.packs.parts import KeybindSource
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


def conflict_warnings(binding: Binding, store: KeybindSource) -> list[str]:
    """What to tell the player if their push-to-talk button also does something in game."""
    from tycho.ptt import windows as win
    from tycho.ptt.devices import describe

    code = game_code(binding, win.scan_code)
    if code is None:
        return []
    names = list(dict.fromkeys(a.label or a.name for a in conflicts(code, store.keybinds)))
    if not names:
        return []
    shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    return [
        f"{describe(binding)} is also bound in game ({shown}). The game sees the press "
        f"too, so choose another button with `{TYCHO} bind`, or unbind it in game."
    ]
