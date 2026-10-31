"""The safety gate: the only way from a decision to the hands (architecture 4.7, ADR 10, ADR 11).

It's independent of every model. Nothing a router, brain or fast-path match says
can skip it. In 0.1 it refuses confirm-grade actions outright; asking out loud
and acting on "confirm" arrives in 0.2 (issue #19).

It also refuses to press a key that a confirm-grade action shares. Keys mean
different things in different modes: left mouse engages the quantum drive in
quantum mode, but fires the guns in combat mode, and Tycho can't yet see which
mode the ship is in.
"""

from dataclasses import dataclass

from tycho.commands.matcher import Command
from tycho.game.keybinds import Action, Keybinds
from tycho.input.plan import pressable_input

CONFIRM_GRADE = frozenset(
    {
        *("v_eject", "v_emergency_exit", "v_self_destruct", "v_jettison_volatile_cargo"),
        "v_toggle_jump_request",
        *("v_weapon_preset_attack", "v_weapon_toggle_launch_missile", "v_weapon_launch_missile"),
        *("attack1", "respawn", "force_respawn", "incapacitatedRespawn"),
    }
)
"""Actions that are hard to undo: losing the ship, leaving it, jumping systems, or firing.

Quantum engage isn't here: it's routine, and the author decided it shouldn't need
confirming (2026-10-30)."""

_DANGER_WORDS = (
    *("eject", "self_destruct", "emergency_exit", "jettison", "respawn"),
    *("jump_request", "launch_missile", "attack", "fire"),
)
"""A backstop for actions a game patch adds or renames before the list above is updated."""

_NOT_DANGER = ("fire_mode", "firemode", "staggered_fire", "cinematic", "cycle_attacker")
"""Harmless actions the words above would catch: choosing a fire mode, the missile
camera, or cycling targets to whoever is attacking you."""


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    reason: str = ""


def check(command: Command, action: Action, keybinds: Keybinds | None = None) -> Verdict:
    """Whether ``action`` may be pressed for ``command`` without asking first."""
    name = action.label or action.name
    if command.confirm or is_confirm_grade(action.name):
        return Verdict(False, f"{name} needs your confirmation, which Tycho can't ask for yet")
    if keybinds is not None and (risky := shares_key_with_danger(action, keybinds)):
        key = pressable_input(action)
        return Verdict(
            False,
            f"{name} is on {key}, which is also {risky.label or risky.name}. "
            "Give it a key of its own (the recommended keybinds do).",
        )
    return Verdict(True)


def is_confirm_grade(action_name: str) -> bool:
    name = action_name.lower()
    if action_name in CONFIRM_GRADE:
        return True
    if any(word in name for word in _NOT_DANGER):
        return False
    return any(word in name for word in _DANGER_WORDS)


def shares_key_with_danger(action: Action, keybinds: Keybinds) -> Action | None:
    """A confirm-grade action bound to the very key Tycho would press for ``action``."""
    key = pressable_input(action)
    if key is None:
        return None
    for other in keybinds:
        if other.name != action.name and key in other.inputs and is_confirm_grade(other.name):
            return other
    return None
