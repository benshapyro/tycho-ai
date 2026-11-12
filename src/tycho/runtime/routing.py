"""Routing: what one corrected utterance is (ADR 10, ADR 14, #55).

Pure: no clock, no threads, no state. The session and the brain eval both call it,
so the eval grades the path a player's words really take.

In order, a whole utterance is:
1. a word to the Tile, while the Tile is on ("keep that up", ADR 16);
2. "say that again" (#222);
3. a reply to a question, "confirm" or "cancel" (ADR 13);
4. a fast-path command;
5. otherwise, the brain's, if there is one.

What the session does with 1 to 3 depends on what it last said and asked, so
they come back as ``Reserved`` and the session answers them itself.
"""

from dataclasses import dataclass
from typing import Literal

from tycho.brain import Decision, Reply, commands_for
from tycho.commands import Command, CommandMatcher, Defer
from tycho.commands.reserved import (
    CANCEL_WORDS,
    CONFIRM_WORDS,
    SAY_AGAIN_WORDS,
    TILE_WORDS,
)
from tycho.packs import Safety
from tycho.text import normalize

type TileWord = Literal["pin", "clear", "again", "show"]
type ReservedWord = TileWord | Literal["say_again", "confirm", "cancel"]

_TILE: dict[TileWord, frozenset[str]] = {
    "pin": TILE_WORDS["pin"],
    "clear": TILE_WORDS["clear"],
    "again": TILE_WORDS["again"],
    "show": TILE_WORDS["show"],
}


@dataclass(frozen=True, slots=True)
class Reserved:
    """One of the session's own words: it answers them with no command or brain."""

    word: ReservedWord


@dataclass(frozen=True, slots=True)
class Picked:
    """Commands to plan and gate, from the fast path or the brain."""

    commands: tuple[Command, ...]
    by_brain: bool = False
    say: str = ""
    """The brain's line about what it did, if it chose them."""


@dataclass(frozen=True, slots=True)
class ToBrain:
    """The fast path couldn't match it: the brain decides."""

    reason: str


@dataclass(frozen=True, slots=True)
class NotUnderstood:
    """The fast path couldn't match it, and there's no brain to ask."""

    reason: str


type Routed = Reserved | Picked | ToBrain | NotUnderstood


def tile_command(text: str) -> TileWord | None:
    """``"pin"``, ``"clear"``, ``"again"`` or ``"show"`` if the whole utterance is one."""
    said = " ".join(normalize(text))
    return next((what for what, phrases in _TILE.items() if said in phrases), None)


def is_say_again(text: str) -> bool:
    """Whether the whole utterance asks to hear the last answer again."""
    return " ".join(normalize(text)) in SAY_AGAIN_WORDS


def reply_to_question(text: str) -> Literal["confirm", "cancel"] | None:
    """``"confirm"`` or ``"cancel"`` if the whole utterance is one of those replies."""
    said = " ".join(normalize(text))
    if said in CONFIRM_WORDS:
        return "confirm"
    if said in CANCEL_WORDS:
        return "cancel"
    return None


def route(text: str, matcher: CommandMatcher, *, tile: bool = False, brain: bool = True) -> Routed:
    """Where ``text`` (already through the lexicon) goes. ``tile``: the Tile is on, so
    its words are the Tile's. ``brain``: there's a brain to ask what the fast path can't
    match."""
    if tile and (what := tile_command(text)) is not None:
        return Reserved(what)
    if is_say_again(text):
        return Reserved("say_again")
    if (reply := reply_to_question(text)) is not None:
        return Reserved(reply)
    match = matcher.match(text)
    if not isinstance(match, Defer):
        return Picked(match.commands)
    return ToBrain(match.reason) if brain else NotUnderstood(match.reason)


def from_brain(
    decision: Decision, matcher: CommandMatcher, lists: Safety | None = None
) -> Picked | Reply:
    """The brain's decision as commands to plan, exactly as the fast path's are, or
    its reply. Raises ``BrainError`` if it picked something that isn't a command."""
    if isinstance(decision, Reply):
        return decision
    return Picked(commands_for(decision, matcher, lists), by_brain=True, say=decision.say)
