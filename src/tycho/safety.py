"""The safety gate: the only way from a decision to the hands (architecture 4.7, ADR 10, ADR 11).

It's independent of every model. Nothing a router, brain or fast-path match says
can skip it. In 0.1 it refuses confirm-grade actions outright; asking out loud
and acting on "confirm" arrives in 0.2 (issue #19).

It also refuses to press a key that a confirm-grade action shares, unless the
command makes every such use impossible first. Keys mean different things in
different modes: left mouse engages the quantum drive in quantum mode, but fires
the guns in guns mode, and a handgun on foot. "Engage quantum" may click it
because it first sets NAV (ship weapons offline) and quantum mode (no guns,
missiles, mining or salvage). That leaves on foot, a risk the author accepted
for that command, since Tycho only hears what the player says to it on purpose.
"""

from dataclasses import dataclass

from tycho.commands.matcher import Command
from tycho.game.gamelog import QUANTUM_ROUTE
from tycho.game.keybinds import Action, Keybinds
from tycho.input.plan import pressable_input

CONFIRM_GRADE = frozenset(
    {
        *("v_eject", "v_emergency_exit", "v_self_destruct", "v_jettison_volatile_cargo"),
        *("v_weapon_preset_attack", "v_weapon_toggle_launch_missile", "v_weapon_launch_missile"),
        *("attack1", "respawn", "force_respawn", "incapacitatedRespawn"),
    }
)
"""Actions that are hard to undo: losing the ship, leaving it, or firing.

Quantum engage and jump-point jumps aren't here: they're routine travel, and the
author decided they shouldn't need confirming (2026-10-30 and 2026-10-31)."""

_DANGER_WORDS = (
    *("eject", "self_destruct", "emergency_exit", "jettison", "respawn"),
    *("launch_missile", "attack", "fire"),
)
"""A backstop for actions a game patch adds or renames before the list above is updated."""

_NOT_DANGER = ("fire_mode", "firemode", "staggered_fire", "cinematic", "cycle_attacker")
"""Harmless actions the words above would catch: choosing a fire mode, the missile
camera, or cycling targets to whoever is attacking you."""


_MODE_GUARDS: dict[str, frozenset[str]] = {
    # NAV takes the ship's weapons offline.
    "v_master_mode_set_nav": frozenset({"spaceship_weapons", "spaceship_missiles"}),
    # Operator modes are exclusive: in quantum mode the trigger engages the drive.
    "v_set_quantum_mode": frozenset(
        {"spaceship_weapons", "spaceship_missiles", "spaceship_mining", "spaceship_salvage"}
    ),
}
"""Setup actions, and the control contexts (actionmaps) they switch off."""

_STATE_GUARDS: dict[str, frozenset[str]] = {
    # Plotting a route takes the pilot seat, and leaving it clears the fact: not on foot.
    QUANTUM_ROUTE: frozenset({"player"}),
}
"""Required facts, and the control contexts they rule out."""

ACCEPTABLE_RISKS = frozenset({"player"})
"""Contexts a command may accept the risk of. Ship contexts aren't: a mode switches those off."""

_REQUIREMENTS = {
    QUANTUM_ROUTE: "no quantum route is plotted (from Game.log)",
}


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    reason: str = ""


def check(
    command: Command,
    action: Action,
    keybinds: Keybinds | None = None,
    facts: frozenset[str] = frozenset(),
) -> Verdict:
    """Whether ``action`` may be pressed for ``command`` without asking first.

    ``facts`` is what the game state shows now; the command's ``requires`` must be in it.
    """
    name = action.label or action.name
    if command.confirm or is_confirm_grade(action.name):
        return Verdict(False, f"{name} needs your confirmation, which Tycho can't ask for yet")
    if missing := [fact for fact in command.requires if fact not in facts]:
        return Verdict(False, "; ".join(_REQUIREMENTS.get(fact, fact) for fact in missing))
    if keybinds is not None and (risky := shares_key_with_danger(action, keybinds, command)):
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


def shares_key_with_danger(
    action: Action, keybinds: Keybinds, command: Command | None = None
) -> Action | None:
    """A confirm-grade action bound to the very key Tycho would press for ``action``.

    Ones in a control context that ``command``'s setup or requirements switch off
    don't count.
    """
    key = pressable_input(action)
    if key is None:
        return None
    off = switched_off(command) if command is not None else frozenset[str]()
    for other in keybinds:
        if (
            other.name != action.name
            and key in other.inputs
            and is_confirm_grade(other.name)
            and other.actionmap not in off
        ):
            return other
    return None


def switched_off(command: Command) -> frozenset[str]:
    """Control contexts that can't act once ``command``'s setup and requirements hold."""
    off: set[str] = set()
    for name in command.setup:
        off |= _MODE_GUARDS.get(name, frozenset())
    for fact in command.requires:
        off |= _STATE_GUARDS.get(fact, frozenset())
    off |= ACCEPTABLE_RISKS & set(command.accepted_risks)
    return frozenset(off)


def known_facts() -> frozenset[str]:
    """Facts a command may require: the ones the gate knows how to explain and rely on."""
    return frozenset(_REQUIREMENTS)
