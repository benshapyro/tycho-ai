"""The safety gate: the only way from a decision to the hands (architecture 4.7, ADR 10, ADR 11).

It's independent of every model. Nothing a router, brain or fast-path match says
can skip it. Confirm-grade actions are asked about: the session plays a question
sound and acts only on a spoken "confirm" (ADR 13).

It also refuses to press a key that a confirm-grade action shares, unless the
command makes every such use impossible first. Keys mean different things in
different modes: left mouse engages the quantum drive in quantum mode, but fires
the guns in guns mode, and a handgun on foot. "Engage quantum" may click it
because it first sets NAV (ship weapons offline) and quantum mode (no guns,
missiles, mining or salvage). That leaves on foot, a risk the author accepted
for that command, since Tycho only hears what the player says to it on purpose.

In advisor mode (#191, ADR 18) the gate's first check refuses every press, so nothing
anything says gets a key pressed in a game whose rules forbid it.

The rules are here; the lists they use (which actions ask first, which modes switch a
control context off) are the game pack's ``safety.toml`` (ADR 18). Each function takes
``lists``, Star Citizen's when None.
"""

import re
from dataclasses import dataclass
from typing import Final

from tycho.commands.matcher import Command
from tycho.game.keybinds import MODIFIERS, Action, Keybinds, KeyInput
from tycho.input.plan import pressable_input
from tycho.packs import Safety, built_in

_DANGER_WORDS = (
    *("eject", "self_destruct", "emergency_exit", "jettison", "respawn"),
    *("launch_missile", "attack", "fire"),
)
"""The backstop: an action whose name holds one of these asks first, whatever the game
pack's lists say, so a pack can add danger but never remove it (ADR 18). It also
catches actions a game patch adds or renames before the pack's list is updated. Only a
pack that ships inside Tycho may name harmless exceptions (``not_danger``)."""

STAR_CITIZEN: Final = built_in().safety
"""Star Citizen's lists (its pack's safety.toml): the gate's lists when none are given."""

CONFIRM_GRADE = STAR_CITIZEN.confirm
TRAVEL = STAR_CITIZEN.travel
ACCEPTABLE_RISKS = STAR_CITIZEN.accepted_risks


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    reason: str = ""
    ask: bool = False
    """Not allowed yet, but the player may confirm it. Refusals for any other reason can't be."""


def check(
    command: Command,
    action: Action,
    keybinds: Keybinds | None = None,
    facts: frozenset[str] = frozenset(),
    *,
    confirmed: bool = False,
    setup_done: int | None = None,
    lists: Safety | None = None,
) -> Verdict:
    """Whether ``action`` may be pressed for ``command`` now.

    ``facts`` is what the game state shows now; the command's ``requires`` must be in it.
    ``confirmed`` means the player has said "confirm" to this request; every other
    check still applies. ``setup_done`` is how many of the command's setup actions
    will have been pressed before this one (None: all of them). A setup key is
    checked in the mode the game is in before it, not after.
    """
    lists = lists or STAR_CITIZEN
    if lists.keys_off:
        # First, whatever asks (#191): no command, sequence, brain pick or "confirm"
        # gets a key pressed in advisor mode. Not a question: confirming can't change it.
        return Verdict(False, lists.keys_off)
    name = action.label or action.name
    if missing := [fact for fact in command.requires if fact not in facts]:
        return Verdict(False, "; ".join(lists.facts.get(fact, fact) for fact in missing))
    if keybinds is not None and (clash := key_clash(action, keybinds, command, setup_done, lists)):
        risky, bound = clash
        key = pressable_input(action)
        danger = risky.label or risky.name
        # Name the key that clashes: for "ralt+x", Eject may be on "ralt" alone (#208).
        also = (
            f"which is also {danger}"
            if bound == key
            else f"which holds down {bound}, and {bound} is {danger}"
        )
        return Verdict(
            False,
            f"{name} is on {key}, {also}. Give it a key of its own (the recommended keybinds do).",
        )
    if not confirmed and (command.confirm or is_confirm_grade(action.name, lists)):
        return Verdict(False, f"{name} needs your confirmation", ask=True)
    return Verdict(True)


def spoken_name(action: Action) -> str:
    """How Tycho names an action when asking about it: the game's label, without
    notes like "(Toggle)"."""
    return re.sub(r"\s*\([^)]*\)", "", action.label).strip() or action.name


def is_travel(command: Command, lists: Safety | None = None) -> bool:
    travel = (lists or STAR_CITIZEN).travel
    return any(name in travel for name in command.actions)


def is_confirm_grade(action_name: str, lists: Safety | None = None) -> bool:
    lists = lists or STAR_CITIZEN
    name = action_name.lower()
    if action_name in lists.confirm:
        return True
    if any(word in name for word in lists.not_danger):
        return False
    return any(word in name for word in _DANGER_WORDS)


def shares_key_with_danger(
    action: Action,
    keybinds: Keybinds,
    command: Command | None = None,
    setup_done: int | None = None,
    lists: Safety | None = None,
) -> Action | None:
    """A confirm-grade action bound to the very key Tycho would press for ``action``, or
    to modifiers alone that the press holds down on the way ("ralt" for "ralt+x").

    Ones in a control context that ``command``'s setup or requirements switch off
    don't count.
    """
    clash = key_clash(action, keybinds, command, setup_done, lists)
    return clash[0] if clash is not None else None


def key_clash(
    action: Action,
    keybinds: Keybinds,
    command: Command | None = None,
    setup_done: int | None = None,
    lists: Safety | None = None,
) -> tuple[Action, KeyInput] | None:
    """As ``shares_key_with_danger``, with the confirm-grade action's binding that the
    press would also press: the same key, or the modifiers it holds (#208)."""
    key = pressable_input(action)
    if key is None:
        return None
    off = switched_off(command, setup_done, lists) if command is not None else frozenset[str]()
    for other in keybinds:
        if other.name == action.name or not is_confirm_grade(other.name, lists):
            continue
        if other.actionmap in off:
            continue
        if bound := next((b for b in other.inputs if _pressed_by(key, b)), None):
            return other, bound
    return None


def _pressed_by(press: KeyInput, bound: KeyInput) -> bool:
    """Whether pressing ``press`` also presses ``bound``: the same input, or a binding of
    modifiers only, all held for the press (the modifiers go down before the main key)."""
    if bound == press:
        return True
    held = {bound.key, *bound.modifiers}
    return held <= MODIFIERS and held <= set(press.modifiers)


def switched_off(
    command: Command, setup_done: int | None = None, lists: Safety | None = None
) -> frozenset[str]:
    """Control contexts that can't act once ``command``'s requirements hold and the first
    ``setup_done`` of its setup actions (all of them, if None) have been pressed."""
    lists = lists or STAR_CITIZEN
    off: set[str] = set()
    setup = command.setup if setup_done is None else command.setup[:setup_done]
    for name in setup:
        off |= lists.mode_guards.get(name, frozenset())
    for fact in command.requires:
        off |= lists.state_guards.get(fact, frozenset())
    off |= lists.accepted_risks & set(command.accepted_risks)
    return frozenset(off)


def known_facts(lists: Safety | None = None) -> frozenset[str]:
    """Facts a command may require: the ones the gate knows how to explain and rely on."""
    return frozenset((lists or STAR_CITIZEN).facts)
