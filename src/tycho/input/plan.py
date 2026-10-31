"""Turning a game action into exact presses: which keys, in what order, for how long.

Timing follows the game's own activation modes (defaultProfile.xml):
- a "tap" only counts if released within ``release_threshold`` (0.25 s);
- a "delayed_press" only counts if held past ``press_threshold`` (0.15 to 1.5 s);
- "hold" modes act for as long as the key is down;
- double-taps need two quick presses.
Pressing for the wrong length can trigger a different action on the same key:
F6 tapped is "Engines - Increase"; F6 held is "Engines - Set to Max".
"""

from dataclasses import dataclass

from tycho.game.keybinds import Action, ActivationMode, KeyInput
from tycho.input.keys import Code, code_for

TAP_SECONDS = 0.06
"""How long a plain press or tap is held: long enough to register, short of any threshold."""
DEFAULT_HOLD_SECONDS = 0.6
"""For "(Hold)" actions with no stated duration: past the 0.25 s tap window."""
THRESHOLD_MARGIN = 0.12
"""Extra time past a delayed-press threshold, so it reliably counts."""
TAP_GAP_SECONDS = 0.08
"""Between the presses of a double-tap."""


class NotPressable(ValueError):
    """The action has no key or mouse button Tycho can press."""


@dataclass(frozen=True, slots=True)
class Press:
    """Everything needed to trigger one action once."""

    action: str
    keys: tuple[Code, ...]
    """Modifiers first, the main key last. Pressed in order, released in reverse."""
    hold_seconds: float
    taps: int = 1
    gap_seconds: float = TAP_GAP_SECONDS
    label: str = ""

    @property
    def main(self) -> Code:
        return self.keys[-1]


def plan(
    action: Action, modes: dict[str, ActivationMode], hold_seconds: float | None = None
) -> Press:
    """How to press ``action``. ``hold_seconds`` overrides how long a hold action is held."""
    key = pressable_input(action)
    if key is None:
        raise NotPressable(f"{action.label or action.name} has no key Tycho can press")
    codes = tuple(code_for(name) for name in (*key.modifiers, key.key))
    mode = modes.get(action.activation or "press") or ActivationMode(action.activation or "press")
    return Press(
        action=action.name,
        keys=codes,
        hold_seconds=0.0 if not codes[-1].holdable else _hold_time(action, mode, hold_seconds),
        taps=action.taps,
        label=action.label,
    )


def pressable_input(action: Action) -> KeyInput | None:
    """The first binding Tycho can press, preferring the keyboard over the mouse."""
    usable = [i for i in action.inputs if i.pressable]
    return next((i for i in usable if i.device == "keyboard"), usable[0] if usable else None)


def _hold_time(action: Action, mode: ActivationMode, requested: float | None) -> float:
    if mode.press_threshold > 0:
        # Delayed press or delayed hold: must pass the threshold, then hold as asked.
        return mode.press_threshold + THRESHOLD_MARGIN + (requested or 0.0)
    if mode.release_threshold > 0:
        # A tap: release well inside the window, even if a longer hold was asked for.
        return min(TAP_SECONDS, mode.release_threshold / 2)
    if requested is not None:
        return requested
    if mode.name.startswith(("hold", "delayed_hold")) or "(hold)" in action.label.lower():
        # Acts only while held (e.g. boost), or shares its key with a tap action
        # ("Engines - Set to Max (Hold)" on F6). Callers can ask for a longer hold.
        return DEFAULT_HOLD_SECONDS
    return TAP_SECONDS
