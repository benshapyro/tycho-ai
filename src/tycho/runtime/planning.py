"""Planning: the presses for one request, checked by the safety gate (ADR 10, ADR 13, #55).

Pure: given the commands, the keybinds and what the game state shows, it says what
would be pressed, or why nothing will be, or what must be confirmed first. The
session and the brain eval both call it, so the eval grades the presses Tycho
would really make.

The whole request is planned before anything is pressed: if any part can't be
done, none of it is, so the ship is never left half-way through a request. A
confirmation plans again from scratch, with the keybinds as they are then.

This is where a game pack's safety lists reach the gate (ADR 18): every press of
every request passes through ``plan_request``.
"""

from dataclasses import dataclass
from typing import Literal

from tycho import safety
from tycho.commands import Command, resolve
from tycho.game.keybinds import Action, Keybinds
from tycho.input import NotPressable, Press, UnsupportedKey, plan
from tycho.packs import Safety

CHAIN_BEAT_SECONDS = 0.15
"""A pause between the actions of a chain, so the game registers each one."""


@dataclass(frozen=True, slots=True)
class Steps:
    """Every press, in order, each with how long to wait before it."""

    presses: tuple[tuple[float, Press], ...]

    @property
    def actions(self) -> tuple[str, ...]:
        """The game actions pressed, setup included, in order."""
        return tuple(press.action for _, press in self.presses)


@dataclass(frozen=True, slots=True)
class Refusal:
    """Nothing is pressed, and confirming can't change that."""

    status: Literal["no_key", "refused"]
    detail: str


@dataclass(frozen=True, slots=True)
class Questions:
    """Nothing is pressed until the player says "confirm" (ADR 13)."""

    reasons: tuple[str, ...]
    """The gate's own words, one per press it asks about."""
    asking_about: tuple[str, ...]
    """The spoken name of each action it asks about."""

    def question(self, address: str = "") -> str:
        """What Tycho asks, from the gate's own words, never the brain's: the player
        must hear what they'd be confirming, not what the brain says it is."""
        ending = f"Say confirm, {address}." if address else "Say confirm."
        return f"{', '.join(dict.fromkeys(self.asking_about))}? {ending}"

    @property
    def detail(self) -> str:
        return f'{"; ".join(self.reasons)}: hold push-to-talk and say "confirm"'


type Planned = Steps | Refusal | Questions


def needs_facts(commands: tuple[Command, ...]) -> bool:
    """Whether planning these needs the game state's facts (reading them may be slow)."""
    return any(step.command.requires for command in commands for step in command.expand())


def announced(commands: tuple[Command, ...]) -> bool:
    """Whether the game itself announces every part of these, so Tycho needn't."""
    return all(step.command.announced for command in commands for step in command.expand())


def plan_request(
    commands: tuple[Command, ...],
    keybinds: Keybinds,
    facts: frozenset[str] = frozenset(),
    *,
    confirmed: bool = False,
    lists: Safety | None = None,
) -> Planned:
    """The presses for ``commands``, or why there are none.

    A sequence is planned step by step, like a chain. A refusal outranks any
    question: if one part is refused, nothing is asked. ``confirmed`` means the
    player has said "confirm" to this request; every other check still applies.
    ``lists`` are the game pack's safety lists (Star Citizen's when None): the one place
    a pack's lists reach the gate (ADR 18).
    """
    steps: list[tuple[float, Press]] = []
    reasons: list[str] = []
    asking_about: list[str] = []
    for part in (step for command in commands for step in command.expand()):
        command = part.command
        resolved = resolve(command, keybinds)
        if resolved.action is None:
            return Refusal(
                "no_key", f"{command.id}: none of {', '.join(command.actions)} has a key"
            )
        setup: list[Action] = []
        for name in command.setup:
            action = keybinds.get(name)
            if action is None or not action.bound:
                return Refusal("no_key", f"{command.id}: {name} needs a key of its own first")
            setup.append(action)
        for position, action in enumerate((*setup, resolved.action)):
            verdict = safety.check(
                command,
                action,
                keybinds,
                facts,
                confirmed=confirmed,
                setup_done=position,
                lists=lists,
            )
            if verdict.ask:
                reasons.append(verdict.reason)
                asking_about.append(safety.spoken_name(action))
            elif not verdict.allowed:
                return Refusal("refused", verdict.reason)
        try:
            for position, action in enumerate((*setup, resolved.action)):
                settle = command.settle_seconds if setup and position == len(setup) else 0.0
                if position == 0:
                    settle = max(settle, part.wait_seconds)
                beat = max(settle, CHAIN_BEAT_SECONDS) if steps else 0.0
                steps.append((beat, plan(action, keybinds.modes)))
        except (NotPressable, UnsupportedKey) as e:
            return Refusal("no_key", str(e))
    if reasons:
        return Questions(tuple(reasons), tuple(asking_about))
    return Steps(tuple(steps))
