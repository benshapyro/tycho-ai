"""What Claude is told before every request: the rules, the player's actions and sequences.

It is cached on Anthropic's side, so it must be byte-for-byte the same from one
request to the next: sorted, with nothing that changes per request (no times, no
state). It changes only when the keybinds or the commands file do. What does
change (today's date) goes with each request instead (see ``claude.history``).

Actions nobody would ask for by voice are left out (#59): fewer options, fewer
wrong picks, and a smaller briefing.

The fixed text is the core (``core.md``: the rules every game shares) placed into
the game pack's ``briefing.md`` (ADR 18). The game facts (the pack's ``facts.md``)
are what changed since Claude learned about the game, kept by hand: update them each
patch. Facts only, never keys: Tycho uses the player's own keybinds.
"""

from importlib import resources

from tycho.commands import CommandMatcher, resolve
from tycho.game.keybinds import Keybinds
from tycho.input.plan import pressable_input
from tycho.packs import GamePack, built_in
from tycho.packs.pack import MARKER


def core_sections() -> dict[str, str]:
    """The core of the briefing (``core.md``), by section: the rules every pack shares."""
    text = resources.files("tycho.brain").joinpath("core.md").read_text("utf-8")
    parts = MARKER.split(text)[1:]
    return {name: body.strip("\n") for name, body in zip(parts[::2], parts[1::2], strict=True)}


def instructions(pack: GamePack | None = None) -> str:
    """The briefing's fixed text: the pack's part, with the core sections in their places.
    For Star Citizen it's byte for byte the text from before packs (a test pins it), so
    the prompt cache and eval scores carry over."""
    pack = pack or built_in()
    core = core_sections()
    return MARKER.sub(lambda found: core[found.group(1)], pack.briefing)


def briefed(action_name: str, actionmap: str, pack: GamePack | None = None) -> bool:
    """Whether the brain is offered an action at all."""
    pack = pack or built_in()
    if actionmap in pack.unbriefed_actionmaps:
        return False
    return pack.continuous is None or not pack.continuous.fullmatch(action_name)


def bound_actions(
    keybinds: Keybinds, keep: frozenset[str] = frozenset(), pack: GamePack | None = None
) -> dict[str, str]:
    """Every action Tycho could press for the player, with its line in the briefing.
    Actions in ``keep`` (those a command presses) are offered even if they'd be left out."""
    lines: dict[str, str] = {}
    for action in keybinds:
        key = pressable_input(action)
        if key is None or not action.label.strip() or action.name in lines:
            continue
        if action.name not in keep and not briefed(action.name, action.actionmap, pack):
            continue
        # No key: Claude picks by what an action does, and Tycho presses its key.
        lines[action.name] = f"{action.name} | {action.label}"
    return lines


def offered_actions(
    keybinds: Keybinds, matcher: CommandMatcher, pack: GamePack | None = None
) -> dict[str, str]:
    """The actions the brain may pick: what the briefing lists, and nothing else."""
    keep = frozenset(line.split(" | ", 1)[0] for line in command_phrasings(keybinds, matcher))
    return bound_actions(keybinds, keep, pack)


BRIEFED_PHRASES = 3
"""How many of a command's phrases Claude sees: enough to recognise it said another way.
The rest are for the fast path, which matches them without Claude."""


def command_phrasings(keybinds: Keybinds, matcher: CommandMatcher) -> list[str]:
    """Each command the player can use, with the action it presses and how it's said."""
    lines: list[str] = []
    for command in matcher.commands:
        if command.is_sequence or not command.say:
            continue
        resolved = resolve(command, keybinds)
        if resolved.action is None:
            continue
        lines.append(f"{resolved.action.name} | {'; '.join(_examples(command.say))}")
    return sorted(lines)


UNBOUND_PHRASES = 2
"""How many of a command's phrases the no-key list shows: enough to recognise it."""


def unbound_commands(keybinds: Keybinds, matcher: CommandMatcher) -> list[str]:
    """Each command Tycho can't do on this PC, because none of its actions has a key,
    with the game action to bind: so the brain says so rather than pressing something
    near it ("lock the doors" pressed Open/Close Doors, #28)."""
    lines: list[str] = []
    for command in matcher.commands:
        if command.is_sequence or not command.say or not command.actions:
            continue
        if resolve(command, keybinds).action is not None:
            continue
        best = keybinds.get(command.actions[0])
        to_bind = best.label.strip() if best is not None and best.label.strip() else ""
        said = "; ".join(command.say[:UNBOUND_PHRASES])
        lines.append(f"{command.id} | {said} | {to_bind or command.actions[0]}")
    return sorted(lines)


def _examples(say: tuple[str, ...]) -> list[str]:
    """The first phrase, which names the command, and the shortest others: how it's
    said in a few words ("gear down"), not the long variants after it."""
    rest = sorted(say[1:], key=len)[: BRIEFED_PHRASES - 1]
    return [*say[:1], *rest]


def game_facts(pack: GamePack | None = None) -> str:
    """The hand-kept sheet of what's true in the current patch (the pack's facts.md)."""
    return (pack or built_in()).facts


def briefing(keybinds: Keybinds, matcher: CommandMatcher, pack: GamePack | None = None) -> str:
    """The whole briefing, for the pack's game (Star Citizen when none is given)."""
    actions = offered_actions(keybinds, matcher, pack)
    sequences = sorted(
        f"{c.id} | {', then '.join(s.command.id for s in c.steps)} | {'; '.join(c.say)}"
        for c in matcher.commands
        if c.is_sequence
    )
    return (
        instructions(pack)
        + "\nGame facts:\n"
        + game_facts(pack)
        + "\n\nThe player's sequences (name | steps | how it's said):\n"
        + ("\n".join(sequences) or "(none)")
        + "\n\nThe player's commands (the action it presses | how it's said):\n"
        + ("\n".join(command_phrasings(keybinds, matcher)) or "(none)")
        + "\n\nThe player's commands with no key (name | how it's said | what to bind):\n"
        + ("\n".join(unbound_commands(keybinds, matcher)) or "(none)")
        + "\n\nThe player's bound actions (name | what it does):\n"
        + "\n".join(sorted(actions.values()))
        + "\n"
    )
