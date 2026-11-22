"""Turning a matched command into the player's actual key."""

from dataclasses import dataclass

from tycho.commands.matcher import Command
from tycho.keybinds import Action, Keybinds


@dataclass(frozen=True, slots=True)
class Resolved:
    command: Command
    action: Action | None
    """The first of the command's actions with a key bound, or None if none has one."""
    fallback: bool = False
    """True when the best action had no key and a later one (usually a toggle) was used."""

    @property
    def blind(self) -> bool:
        """A toggle pressed for a direction ("gear down" with only the toggle bound), so
        which way it went can't be told from the key (#13, #29)."""
        return (
            self.action is not None
            and (self.fallback or self.command.blind)
            and is_toggle(self.action)
        )


_TOGGLES = ("toggle", "swap", "mode_cycle")
"""Words in an action's name that mean it flips or cycles a state, so pressing it from
an unknown state can leave it either way. "Cycle attacker" isn't one: it picks a target."""


def is_toggle(action: Action) -> bool:
    """Whether ``action`` flips a state rather than setting one ("Headlights (Toggle)")."""
    return any(word in action.name.lower() for word in _TOGGLES) or "toggle" in action.label.lower()


def resolve(command: Command, keybinds: Keybinds) -> Resolved:
    for position, name in enumerate(command.actions):
        action = keybinds.get(name)
        if action is not None and action.bound:
            return Resolved(command, action, fallback=position > 0)
    return Resolved(command, None)
