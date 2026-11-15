"""The player's own keys for commands, in words (#386, #392).

With key actions off Tycho presses nothing, so the brain says how instead, and names
the player's key: for a phrase the fast path matched (a line with the request) and for
anything else (a list in the keys-off briefing).
"""

from tycho.commands.matcher import Command
from tycho.commands.resolve import resolve
from tycho.input.plan import pressable_input
from tycho.keybinds import Action, Keybinds, key_text
from tycho.safety import spoken_name


def own_keys(matched: tuple[Command, ...], keybinds: Keybinds) -> str:
    """The player's own keys for built-in commands said with key actions off, for the
    brain's advice to name ("Night Vision on Right Alt + N"), or "" when Tycho can't
    read this game's keybinds (#386). A sequence names each step's key, and a command
    that switches mode first names that key before its own. Tycho sees only keyboard
    and mouse keys, so one with none says just that: it may be on a stick."""
    if not len(keybinds):
        return ""
    keys: list[str] = []
    for command in matched:
        for step in command.expand():
            part = step.command
            for name in part.setup:
                setup = keybinds.get(name)
                keys.append(_key_for(setup, name.replace("_", " ")) + ", first")
            found = resolve(part, keybinds)
            keys.append(_key_for(found.action, part.id.replace("_", " ")))
    return "; ".join(dict.fromkeys(keys))


def _key_for(action: Action | None, said: str) -> str:
    """One action and its key, in the game's words when the keybinds have them."""
    key = pressable_input(action) if action is not None and action.bound else None
    if action is None or key is None:
        return f"{said}: no keyboard or mouse key Tycho can see"
    name = spoken_name(action) if action.label.strip() else said
    return f"{name} on {key_text(key)}"
