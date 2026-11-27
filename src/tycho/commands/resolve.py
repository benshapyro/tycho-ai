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
    def for_direction(self) -> bool:
        """Pressed for a direction it may not set ("gear down" with only a fallback
        bound). If the game pack marks the action a toggle, which way it went can't be
        told from the key (#13, #29): ``Safety.flips``."""
        return self.action is not None and (self.fallback or self.command.blind)


def resolve(command: Command, keybinds: Keybinds) -> Resolved:
    for position, name in enumerate(command.actions):
        action = keybinds.get(name)
        if action is not None and action.bound:
            return Resolved(command, action, fallback=position > 0)
    return Resolved(command, None)


def on_stick(command: Command, keybinds: Keybinds) -> Action | None:
    """For a command with no key: its first action that's bound on the player's stick
    instead (#622). Tycho never presses that; it says it's on the stick."""
    if resolve(command, keybinds).action is not None:
        return None
    return next((a for n in command.actions if (a := keybinds.on_stick(n)) is not None), None)
