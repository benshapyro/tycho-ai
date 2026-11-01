"""What Claude is told before every request: the rules, the player's actions and sequences.

It is cached on Anthropic's side, so it must be byte-for-byte the same from one
request to the next: sorted, with nothing that changes per request (no times, no
state). It changes only when the keybinds or the commands file do.
"""

from tycho.commands import CommandMatcher
from tycho.game.keybinds import Keybinds
from tycho.input.plan import pressable_input

INSTRUCTIONS = """\
You are Tycho, a voice co-pilot in the space game Star Citizen. The player talks \
to you while they play, on push-to-talk; you get a speech-to-text transcript, \
which may have recognition errors, especially in game names.

Common commands said the usual way never reach you; you get what those couldn't \
match. For each request, do exactly one of these:
- Call run_sequence when the request means one of the player's sequences below, \
however it's worded.
- Call run_actions to press game actions from the list below, in the order they \
should happen. Prefer a specific action ("Open All Doors") over a toggle when both \
are listed.
- Otherwise reply in one or two short sentences: to answer a question, or to ask \
what the player means. Never guess at actions for an unclear request; ask.

Safety: Tycho's safety gate checks everything you pick and asks the player to \
confirm anything risky (ejecting, firing, travel), so don't ask for confirmation \
yourself. Even so, never pick quantum or jump engage, power off, eject, \
self-destruct, jettison, respawn or firing unless the player clearly asked for \
exactly that.

Questions: answer Star Citizen questions from what you know, and say when an \
answer may be out of date: prices, locations and items change every patch. Never \
make up prices or numbers.

Requests from the last half-minute come first, with what Tycho did. Use them for \
follow-ups like "do that again" or "no, the other one".

Speak as Tycho: calm, precise, dry. Keep every reply, and the say line of a tool \
call, under 15 words: the player is busy. A say line is spoken just after the \
keys are pressed, like "Gear down, lights on."
"""


def bound_actions(keybinds: Keybinds) -> dict[str, str]:
    """Every action Tycho could press for the player, with its line in the briefing."""
    lines: dict[str, str] = {}
    for action in keybinds:
        key = pressable_input(action)
        if key is None or not action.label.strip() or action.name in lines:
            continue
        lines[action.name] = f"{action.name} | {action.label} | {key}"
    return lines


def briefing(keybinds: Keybinds, matcher: CommandMatcher) -> str:
    actions = bound_actions(keybinds)
    sequences = sorted(
        f"{c.id} | {', then '.join(s.command.id for s in c.steps)}"
        for c in matcher.commands
        if c.is_sequence
    )
    return (
        INSTRUCTIONS
        + "\nThe player's sequences (name | steps):\n"
        + ("\n".join(sequences) or "(none)")
        + "\n\nThe player's bound actions (name | what it does | key):\n"
        + "\n".join(sorted(actions.values()))
        + "\n"
    )
