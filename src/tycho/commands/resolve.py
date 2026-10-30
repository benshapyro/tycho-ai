"""Turning a matched command into the player's actual key."""

from dataclasses import dataclass

from tycho.commands.matcher import Command
from tycho.game.keybinds import Action, Keybinds


@dataclass(frozen=True, slots=True)
class Resolved:
    command: Command
    action: Action | None
    """The first of the command's actions with a key bound, or None if none has one."""
    fallback: bool = False
    """True when the best action had no key and a later one (usually a toggle) was used."""


def resolve(command: Command, keybinds: Keybinds) -> Resolved:
    for position, name in enumerate(command.actions):
        action = keybinds.get(name)
        if action is not None and action.bound:
            return Resolved(command, action, fallback=position > 0)
    return Resolved(command, None)
