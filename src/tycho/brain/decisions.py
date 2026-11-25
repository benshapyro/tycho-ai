"""What the brain can decide, and turning it into commands the gate checks (ADR 14).

The brain's output is untrusted: every action and sequence it names is checked
against the player's own before anything is planned, and an invented name drops
the whole decision. What it picks becomes ordinary commands, so the safety gate,
the planner, spoken confirmation and the action log treat it like the fast path.
"""

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace

from tycho import safety
from tycho.commands import Command, CommandMatcher
from tycho.eyes import Picture
from tycho.keybinds import Keybinds
from tycho.packs import Safety
from tycho.tile import BRAIN_KINDS, Card, from_tool

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
    card: Card | None = None
    """The details, for the Tile (#2): said in a line, read on the card."""
    looked_up: tuple[str, ...] = field(default=(), compare=False)
    """What the brain looked up for it, in a few words each, for the follow-ups (#564).
    Not part of what the reply is: two replies with the same words and card are equal."""


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
    own_keys: str = ""
    """With key actions off, the player's own keys for the built-in command they said
    ("Night Vision on Right Alt + N"), so the advice can name them (#386)."""
    waiting: Callable[[], None] | None = field(default=None, compare=False)
    """Called once if the answer will be slow (a web search has started), so the
    player hears a short line instead of silence (ADR 15)."""
    looking: Callable[[], None] | None = field(default=None, compare=False)
    """Called as each other look-up starts (prices, a ship), so the wait for the answer
    allows for it (play session, 6 November 2026). At once, even if the reply then ends
    in a key press: the press ends the request anyway (#178)."""
    checking: Callable[[], None] | None = field(default=None, compare=False)
    """Called at most once a request, when a reply with look-ups is known not to end in
    a key press, so the player hears "Checking." while Tycho checks, and never just
    before a press (#176, #178)."""
    picture: Picture | None = field(default=None, compare=False, repr=False)
    """The game window, when the player asked Tycho to look (ADR 14, #31): sent with
    this request only, never kept, logged or traced (the trace keeps its size)."""
    may_look: bool = False
    """The player has looking turned on: the brain may offer one (``LOOK_OFFER``) when
    seeing the game would answer and no picture came. It can't take one itself."""
    where: str = ""
    """What the game's log shows now, one line for the header (``situation.told``):
    where the player is, so a look-up keeps to their system (#656). Untrusted text."""


class BrainError(Exception):
    """The brain gave no usable answer: offline, a refusal, a timeout, or bad output."""


class EmptyAct(BrainError):
    """``run_actions`` with no actions and nothing real to say (#666): "unused", or
    empty, which the acts' strict schema makes the model fill in. Not an answer: the
    model is told, and answers on its next call (``rounds.EMPTY_ACT``)."""

    def __init__(self) -> None:
        super().__init__("run_actions: no actions and nothing to say")


class UnboundAction(BrainError):
    """``run_actions`` named actions that aren't bound: invented ("v_noise_launch"), or
    real with no key. The brain is told, with the closest bound names, and picks again
    (#164) rather than the request failing."""

    def __init__(self, unknown: list[str]) -> None:
        super().__init__(f"run_actions: {', '.join(unknown)} isn't one of your bound actions")
        self.unknown = unknown


def parse_tool_call(
    name: str, arguments: object, actions: Collection[str], sequences: Collection[str]
) -> Decision:
    """A tool call from the brain, checked. Raises ``BrainError`` if it can't be used."""
    if not isinstance(arguments, dict):
        raise BrainError(f"{name}: the arguments aren't an object")
    args: dict[str, object] = {str(k): v for k, v in arguments.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    say = line(args.get("say", ""))
    if name == "run_actions":
        picked = args.get("actions")
        if isinstance(picked, list) and not picked:
            # Nothing to press, but a line to say: a reply in the wrong tool, as for
            # "good fight everyone, o7" in the #192 eval after #276. The player
            # hears the line, not an error. With no real line, it's no answer (#666).
            if say:
                return Reply(say)
            raise EmptyAct
        if not isinstance(picked, list) or not picked:
            raise BrainError("run_actions: no actions")
        names = tuple(str(a) for a in picked)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if len(names) > MAX_ACTIONS:
            raise BrainError(f"run_actions: {len(names)} actions is too many")
        if unknown := [a for a in names if a not in actions]:
            raise UnboundAction(unknown)
        return RunActions(names, say)
    if name == "run_sequence":
        sequence = args.get("name")
        if not isinstance(sequence, str) or sequence not in sequences:
            raise BrainError(f"run_sequence: {sequence!r} isn't one of your sequences")
        return RunSequence(sequence, say)
    if name == "show_card":
        try:
            card = from_tool(args)
        except ValueError as e:
            if say and args.get("kind") in BRAIN_KINDS:
                # An empty card with a line to say (#443): the player hears the line,
                # not the error sound, less any sentence pointing at the Tile.
                return Reply(_ON_THE_TILE.sub("", say).strip() or say)
            raise BrainError(f"show_card: {e}") from e
        if not say:
            raise BrainError("show_card: nothing to say")
        return Reply(say, card)
    raise BrainError(f"unknown tool {name!r}")


PLACEHOLDERS = frozenset(
    {
        "unused",
        "none",
        "n/a",
        "na",
        "null",
        "nil",
        "empty",
        "placeholder",
        "nothing",
        "no action",
        "no actions",
        "tbd",
        "todo",
        "undefined",
        "-",
    }
)
"""What a model writes in a ``say`` it has to fill but has nothing for (#666): never
spoken. The acts are strict (#639), so ``say`` is always there, even when empty."""


def line(say: object) -> str:
    """A tool call's ``say`` as a line to speak: cut to ``MAX_SAY``, and empty for a
    placeholder ("unused", "n/a") the player must never hear (#666)."""
    if not isinstance(say, str):
        return ""
    said = say[:MAX_SAY].strip()
    key = said.lower().strip(" .!\"'")
    return "" if not key or key in PLACEHOLDERS else said


_ON_THE_TILE = re.compile(r"(?<=[.!?])\s+[^.!?]*\bon the Tile\b[^.!?]*[.!?]?\s*$", re.IGNORECASE)
"""The last sentence of a card's line when it points at the card ("Two more on the Tile.")."""


def commands_for(
    decision: RunActions | RunSequence, matcher: CommandMatcher, lists: Safety
) -> tuple[Command, ...]:
    """The commands to plan for what the brain picked.

    An action that is a command's own (first) action runs as that command, with its
    setup, waits and accepted risks, exactly as the fast path would. Any other action
    runs bare, with none: a key it shares with a confirm-grade action stays refused.
    Either way, if any command using the action has ``confirm`` or ``requires``, so
    does the pick.
    A bare action counts as announced if an announced command uses it, so Tycho
    doesn't talk over the game. Travel the brain picked always asks first (the
    author's call, 2026-10-31): the game pack's ``travel`` list (``lists``).
    """
    if isinstance(decision, RunSequence):
        sequence = matcher.get(decision.name)
        if sequence is None or not sequence.is_sequence:
            raise BrainError(f"{decision.name!r} isn't a sequence")
        steps = tuple(
            replace(s, command=_ask_before_travel(s.command, lists)) for s in sequence.steps
        )
        return (replace(sequence, steps=steps),)
    own: dict[str, Command] = {}
    users: dict[str, list[Command]] = {}
    for command in matcher.commands:
        if command.actions and not command.is_sequence:
            own.setdefault(command.actions[0], command)
            for name in command.actions:
                users.setdefault(name, []).append(command)
    picked: list[Command] = []
    for name in decision.actions:
        using = users.get(name, [])
        command = own.get(name) or Command(
            id=name, say=(), actions=(name,), announced=any(c.announced for c in using)
        )
        if any(name in c.actions[1:] for c in using):  # a fallback for a direction (#29)
            command = replace(command, blind=True)
        picked.append(_ask_before_travel(_strictest(command, using), lists))
    return tuple(picked)


def _strictest(command: Command, using: list[Command]) -> Command:
    """``command``, asking first and needing every fact if any command that uses its
    action does: a player's ``confirm = true`` holds however the brain names the
    action, as a fallback or one a built-in command also starts with."""
    confirm = command.confirm or any(c.confirm for c in using)
    requires = tuple(dict.fromkeys((*command.requires, *(r for c in using for r in c.requires))))
    if confirm == command.confirm and requires == command.requires:
        return command
    return replace(command, confirm=confirm, requires=requires)


def _ask_before_travel(command: Command, lists: Safety) -> Command:
    if safety.is_travel(command, lists) and not command.confirm:
        return replace(command, confirm=True)
    return command
