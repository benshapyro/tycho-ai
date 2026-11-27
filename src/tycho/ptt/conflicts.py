"""Is the push-to-talk button also bound in game? (ADR 11)

Tycho sees the press but can't stop the game seeing it too, so holding a
button bound to, say, "Landing gear" would toggle the gear while you talk.
Only bindings without modifiers count: a press of Mouse 5 alone doesn't
trigger an action bound to Ctrl + Mouse 5.

A stick button is compared with the game's stick bindings (#622), on the stick
``sticks.game_sticks`` matches to it. When it can't tell which of the game's sticks
it is, every stick counts, and the warning says it may be bound: a missed warning
is worse than one that may not apply.
"""

from collections.abc import Callable

from tycho.findings import TYCHO
from tycho.input.keys import Code, UnsupportedKey, code_for
from tycho.keybinds import Action, Keybinds
from tycho.packs.parts import KeybindSource
from tycho.ptt import sticks
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


def stick_conflicts(binding: Binding, keybinds: Keybinds) -> tuple[list[Action], bool]:
    """Game actions a press of this stick button would trigger, and whether Tycho is sure
    it's the game's stick: False when it couldn't tell, and every stick was checked."""
    button = binding.code + 1  # SDL counts from 0, the game from 1
    which = sticks.game_sticks(binding, keybinds.sticks)
    found = [
        action
        for action in keybinds
        if any(
            s.button == button and (which is None or s.stick in which) for s in action.stick_inputs
        )
    ]
    return found, which is not None


def conflict_warnings(binding: Binding, store: KeybindSource) -> list[str]:
    """What to tell the player if their push-to-talk button also does something in game."""
    from tycho.ptt import windows as win
    from tycho.ptt.devices import describe

    warnings: list[str] = []
    sure = True
    if binding.kind is DeviceKind.JOYSTICK:
        found, sure = stick_conflicts(binding, store.keybinds)
    elif (code := game_code(binding, win.scan_code)) is not None:
        found = conflicts(code, store.keybinds)
    else:
        found = []
    if names := list(dict.fromkeys(a.label or a.name for a in found)):
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        fix = f"choose another button with `{TYCHO} bind`, or unbind it in game."
        if sure:
            warnings.append(
                f"{describe(binding)} is also bound in game ({shown}). The game sees the "
                f"press too, so {fix}"
            )
        else:
            warnings.append(
                f"{describe(binding)} may be bound in game too ({shown}): Tycho can't tell "
                f"which of the game's sticks it's on. If it is, the game sees the press "
                f"too, so {fix}"
            )
    return warnings
