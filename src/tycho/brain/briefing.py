"""What Claude is told before every request: the rules, the player's actions and sequences.

It is cached on Anthropic's side, so it must be byte-for-byte the same from one
request to the next: sorted, with nothing that changes per request (no times, no
state). It changes only when the keybinds or the commands file do. What does
change (today's date) goes with each request instead (see ``claude.history``).

Actions nobody would ask for by voice are left out (#59): fewer options, fewer
wrong picks, and a smaller briefing.

The game facts (facts.md) are what changed since Claude learned about the game,
kept by hand: update them each patch. Facts only, never keys: Tycho uses the
player's own keybinds.
"""

import re
from importlib import resources

from tycho.commands import CommandMatcher, resolve
from tycho.game.keybinds import Keybinds
from tycho.input.plan import pressable_input

INSTRUCTIONS = """\
You are Tycho, a voice co-pilot in the space game Star Citizen. The player talks \
to you while they play, on push-to-talk; you get a speech-to-text transcript, \
which often has recognition errors, especially in game words.

The player's commands are listed below with the usual ways of saying them. Those \
exact wordings never reach you: you get what they couldn't match, which is often \
a command said differently or misheard. "Lending gear dawn" is landing gear down; \
"shield two front" is shields to front. When a request sounds like a command, it \
is that command.

For each request, do exactly one of these:
- Call run_sequence when the request means one of the player's sequences, \
however it's worded.
- Call run_actions to press game actions from the list below, in the order they \
should happen. Prefer a specific action ("Open All Doors") over a toggle when both \
are listed. A request often holds several commands ("gear up and lights off"): \
press every one of them.
- Otherwise reply: to answer a question, to ask what the player means, or to \
answer chat that isn't a request. Never call run_actions with no actions.

Act on your best reading when the actions are harmless and easy to undo: modes, \
doors, lights, power to systems, shields, targeting, and air traffic control \
requests. Ask only when you can't tell at all what was meant. The player can \
always say "no, the other one".

Safety: Tycho's safety gate checks everything you pick and asks the player to \
confirm anything risky (ejecting, firing, travel), so don't ask for confirmation \
yourself. Even so, never pick quantum or jump engage, power off, eject, \
self-destruct, jettison, respawn or firing unless the player clearly asked for \
exactly that: an unclear request is never one of those.

Questions: answer Star Citizen questions from what you know. Lead with a real \
answer: a specific ship, place, item or method. The game facts below are \
current: trust them over what you remember, and say "as of" the patch when you \
use them. For anything else that may have changed, add one short clause such as \
"as of the last patch I know". Never tell the player to \
check a wiki, guide or the community instead of answering. Never make up exact \
prices or numbers; give a rough picture instead.

Each request starts with today's date in brackets, which isn't part of what the \
player said. Use it to judge how far the game may have moved on since what you know.

Requests from the last half-minute come first, with what Tycho did. Use them for \
follow-ups like "do that again" or "no, the other one".

Speak as Tycho: calm, precise, dry. The player is flying, and hears every word.
- The say line of a tool call is a few words, spoken just after the keys are \
pressed: "Gear down, lights on."
- A reply fits the question. Most questions are simple, and get one short \
sentence: "What's a good first fighter?" "The Arrow, or the Gladius if you can \
stretch to it." A clarifying question is one sentence too. Only a question that \
asks for an explanation ("how does refining work?") gets more: a few plain \
sentences, no more than it needs. No preamble, and don't repeat the question.
- Say only what you're confident is true in the game. If you don't know, say so \
in a few words rather than guess.
"""


UNBRIEFED_ACTIONMAPS = frozenset(
    {
        "view_director_mode",  # camera director moves
        "spectator",
        "player_emotes",
        "player_input_optical_tracking",  # head tracking, VR, VOIP
        "prone",
    }
)
"""Action maps of actions nobody asks a co-pilot for."""
CONTINUOUS = re.compile(
    r"v_(pitch|yaw|roll)_\w+|v_strafe_(up|down|left|right|forward|back)|v_move_\w+"
    r"|eva_(strafe|roll)_\w+|zgt_roll_\w+|move(forward|back|left|right)|lean(left|right)"
)
"""Steering and movement held down for as long as it should last: one press means nothing."""


def briefed(action_name: str, actionmap: str) -> bool:
    """Whether the brain is offered an action at all."""
    return actionmap not in UNBRIEFED_ACTIONMAPS and not CONTINUOUS.fullmatch(action_name)


def bound_actions(keybinds: Keybinds, keep: frozenset[str] = frozenset()) -> dict[str, str]:
    """Every action Tycho could press for the player, with its line in the briefing.
    Actions in ``keep`` (those a command presses) are offered even if they'd be left out."""
    lines: dict[str, str] = {}
    for action in keybinds:
        key = pressable_input(action)
        if key is None or not action.label.strip() or action.name in lines:
            continue
        if action.name not in keep and not briefed(action.name, action.actionmap):
            continue
        lines[action.name] = f"{action.name} | {action.label} | {key}"
    return lines


def offered_actions(keybinds: Keybinds, matcher: CommandMatcher) -> dict[str, str]:
    """The actions the brain may pick: what the briefing lists, and nothing else."""
    keep = frozenset(line.split(" | ", 1)[0] for line in command_phrasings(keybinds, matcher))
    return bound_actions(keybinds, keep)


def command_phrasings(keybinds: Keybinds, matcher: CommandMatcher) -> list[str]:
    """Each command the player can use, with the action it presses and how it's said."""
    lines: list[str] = []
    for command in matcher.commands:
        if command.is_sequence or not command.say:
            continue
        resolved = resolve(command, keybinds)
        if resolved.action is None:
            continue
        lines.append(f"{resolved.action.name} | {'; '.join(command.say)}")
    return sorted(lines)


def game_facts() -> str:
    """The hand-kept sheet of what's true in the current patch."""
    return resources.files("tycho.brain").joinpath("facts.md").read_text("utf-8").strip()


def briefing(keybinds: Keybinds, matcher: CommandMatcher) -> str:
    actions = offered_actions(keybinds, matcher)
    sequences = sorted(
        f"{c.id} | {', then '.join(s.command.id for s in c.steps)} | {'; '.join(c.say)}"
        for c in matcher.commands
        if c.is_sequence
    )
    return (
        INSTRUCTIONS
        + "\nGame facts:\n"
        + game_facts()
        + "\n\nThe player's sequences (name | steps | how it's said):\n"
        + ("\n".join(sequences) or "(none)")
        + "\n\nThe player's commands (the action it presses | how it's said):\n"
        + ("\n".join(command_phrasings(keybinds, matcher)) or "(none)")
        + "\n\nThe player's bound actions (name | what it does | key):\n"
        + "\n".join(sorted(actions.values()))
        + "\n"
    )
