"""What the brain can decide, and turning it into commands the gate checks (ADR 14).

The brain's output is untrusted: every action and sequence it names is checked
against the player's own before anything is planned, and an invented name drops
the whole decision. What it picks becomes ordinary commands, so the safety gate,
the planner, spoken confirmation and the action log treat it like the fast path.
"""

from collections.abc import Collection
from dataclasses import dataclass, replace

from tycho import safety
from tycho.commands import Command, CommandMatcher
from tycho.game.keybinds import Keybinds

MAX_ACTIONS = 8
"""More than this in one request is a misunderstanding, not a plan."""
MAX_SAY = 200
"""Characters of a spoken line or reply; anything longer is cut."""


@dataclass(frozen=True, slots=True)
class RunActions:
    actions: tuple[str, ...]
    say: str = ""
    """A short line Tycho speaks once the keys are pressed."""


@dataclass(frozen=True, slots=True)
class RunSequence:
    name: str
    say: str = ""


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    """An answer, or a question back to the player. Nothing is pressed."""


type Decision = RunActions | RunSequence | Reply


@dataclass(frozen=True, slots=True)
class Exchange:
    """One earlier request and how it ended, for follow-ups like "do that again"."""

    said: str
    outcome: str


@dataclass(frozen=True, slots=True)
class BrainRequest:
    """Everything the brain is asked with, in one object, so a new input (a player
    profile, live game state) doesn't change the ``Brain`` Protocol (#54).

    The keybinds decide the cached briefing; everything else changes each request.
    """

    text: str
    keybinds: Keybinds
    recent: tuple[Exchange, ...] = ()
    """Earlier exchanges, oldest first, for follow-ups like "do that again"."""


class BrainError(Exception):
    """The brain gave no usable answer: offline, a refusal, a timeout, or bad output."""


def parse_tool_call(
    name: str, arguments: object, actions: Collection[str], sequences: Collection[str]
) -> Decision:
    """A tool call from the brain, checked. Raises ``BrainError`` if it can't be used."""
    if not isinstance(arguments, dict):
        raise BrainError(f"{name}: the arguments aren't an object")
    args: dict[str, object] = {str(k): v for k, v in arguments.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    say = args.get("say", "")
    say = say[:MAX_SAY].strip() if isinstance(say, str) else ""
    if name == "run_actions":
        picked = args.get("actions")
        if not isinstance(picked, list) or not picked:
            raise BrainError("run_actions: no actions")
        names = tuple(str(a) for a in picked)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if len(names) > MAX_ACTIONS:
            raise BrainError(f"run_actions: {len(names)} actions is too many")
        if unknown := [a for a in names if a not in actions]:
            raise BrainError(f"run_actions: {', '.join(unknown)} isn't one of your bound actions")
        return RunActions(names, say)
    if name == "run_sequence":
        sequence = args.get("name")
        if not isinstance(sequence, str) or sequence not in sequences:
            raise BrainError(f"run_sequence: {sequence!r} isn't one of your sequences")
        return RunSequence(sequence, say)
    raise BrainError(f"unknown tool {name!r}")


def commands_for(
    decision: RunActions | RunSequence, matcher: CommandMatcher
) -> tuple[Command, ...]:
    """The commands to plan for what the brain picked.

    An action that is a command's own (first) action runs as that command, with its
    setup, waits and accepted risks, exactly as the fast path would. Any other action
    runs bare, with none: a key it shares with a confirm-grade action stays refused.
    A bare action counts as announced if an announced command uses it, so Tycho
    doesn't talk over the game. Travel the brain picked always asks first (the
    author's call, 2026-10-31).
    """
    if isinstance(decision, RunSequence):
        sequence = matcher.get(decision.name)
        if sequence is None or not sequence.is_sequence:
            raise BrainError(f"{decision.name!r} isn't a sequence")
        steps = tuple(replace(s, command=_ask_before_travel(s.command)) for s in sequence.steps)
        return (replace(sequence, steps=steps),)
    own: dict[str, Command] = {}
    announced: set[str] = set()
    for command in matcher.commands:
        if command.actions and not command.is_sequence:
            own.setdefault(command.actions[0], command)
            if command.announced:
                announced.update(command.actions)
    return tuple(
        _ask_before_travel(
            own.get(name) or Command(id=name, say=(), actions=(name,), announced=name in announced)
        )
        for name in decision.actions
    )


def _ask_before_travel(command: Command) -> Command:
    if safety.is_travel(command) and not command.confirm:
        return replace(command, confirm=True)
    return command
