"""The safety gate: the only way from a decision to the hands (architecture 4.7, ADR 10, ADR 11).

It's independent of every model. Nothing a router, brain or fast-path match says
can skip it. In 0.1 it refuses confirm-grade actions outright; asking out loud
and acting on "confirm" arrives in 0.2 (issue #19).
"""

from dataclasses import dataclass

from tycho.commands.matcher import Command
from tycho.game.keybinds import Action

CONFIRM_GRADE = frozenset(
    {
        *("v_eject", "v_emergency_exit", "v_self_destruct", "v_jettison_volatile_cargo"),
        *("v_toggle_qdrive_engagement", "v_toggle_jump_request"),
        *("v_weapon_preset_attack", "v_weapon_toggle_launch_missile", "v_weapon_launch_missile"),
        *("attack1", "respawn", "force_respawn", "incapacitatedRespawn"),
    }
)
"""Actions that are hard to undo: losing the ship, leaving it, jumping, or firing."""

_DANGER_WORDS = (
    *("eject", "self_destruct", "emergency_exit", "jettison", "respawn"),
    *("qdrive_engage", "jump_request", "launch_missile", "attack", "fire"),
)
"""A backstop for actions a game patch adds or renames before the list above is updated."""


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    reason: str = ""


def check(command: Command, action: Action) -> Verdict:
    """Whether ``action`` may be pressed for ``command`` without asking first."""
    if command.confirm or is_confirm_grade(action.name):
        name = action.label or action.name
        return Verdict(False, f"{name} needs your confirmation, which Tycho can't ask for yet")
    return Verdict(True)


def is_confirm_grade(action_name: str) -> bool:
    name = action_name.lower()
    return action_name in CONFIRM_GRADE or any(word in name for word in _DANGER_WORDS)
