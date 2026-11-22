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
from tycho.input import NotPressable, Press, UnsupportedKey, plan
from tycho.keybinds import Action, Keybinds
from tycho.packs import Safety
from tycho.runtime.pressing import readable

CHAIN_BEAT_SECONDS = 0.15
"""A pause between the actions of a chain, so the game registers each one."""


@dataclass(frozen=True, slots=True)
class Steps:
    """Every press, in order, each with how long to wait before it."""

    presses: tuple[tuple[float, Press], ...]
    toggled: tuple[str, ...] = ()
    """The spoken names of toggles pressed for a direction, whose result Tycho can't
    see (#13, #29): "Doors" for "open the doors" with only Open/Close Doors bound."""

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
    lists: Safety,
) -> Planned:
    """The presses for ``commands``, or why there are none.

    A sequence is planned step by step, like a chain. A refusal outranks any
    question: if one part is refused, nothing is asked. ``confirmed`` means the
    player has said "confirm" to this request; every other check still applies.
    ``lists`` are the game pack's safety lists: the one place
    a pack's lists reach the gate (ADR 18).
    """
    if lists.keys_off:
        # Advisor mode (#191): refused before anything else, even a missing key, so the
        # player hears why Tycho presses nothing here. safety.check refuses it too.
        return Refusal("refused", lists.keys_off)
    steps: list[tuple[float, Press]] = []
    toggled: list[str] = []
    reasons: list[str] = []
    asking_about: list[str] = []
    for part in (step for command in commands for step in command.expand()):
        command = part.command
        resolved = resolve(command, keybinds)
        if resolved.action is None:
            return Refusal(
                "no_key", f"{command.id}: none of {', '.join(command.actions)} has a key"
            )
        if resolved.blind:
            toggled.append(_toggle_name(resolved.action))
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
    return Steps(tuple(steps), tuple(dict.fromkeys(toggled)))


def _toggle_name(action: Action) -> str:
    """ "Headlights (Toggle)" is "Headlights"; "v_toggle_all_doors", with no label, is
    "All doors"."""
    name = safety.spoken_name(action) if action.label else readable(action.name)
    return name.removeprefix("Toggle ").strip() or name


def toggle_receipt(toggled: tuple[str, ...]) -> str:
    """What Tycho says after pressing toggles for a direction (#13, #29): that it
    pressed them and can't see which way they went, never that it's done. It takes the
    place of the brain's line, which would name the direction ("Doors open")."""
    names = " and ".join(toggled)
    which = "it" if len(toggled) == 1 else "they"
    return f"Toggled {names}. I can't see which way {which} went."
