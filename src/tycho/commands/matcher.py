"""The fast path: recognising common commands on the player's PC, without the language model.

Rules, in order of importance:
1. A wrong action is worse than a slow one. Anything uncertain is deferred to
   the language model, which sees context the matcher can't.
2. A near match never flips meaning: "lights off" can't match "lights on".
3. A chain ("drop the gear and ask for a landing pad") runs on the fast path
   only if every part matches; otherwise the whole request is deferred.

Commands come from the built-in ``commands.toml``, and the player's own
``commands.toml`` can add to them or replace them by id (ADR 14). A sequence is a
command made of other commands, with waits between them.
"""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

from tycho.text import normalize, similarity

_LEADING_FILLER = (
    ("okay",),
    ("ok",),
    ("hey",),
    ("tycho",),
    ("please",),
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("go", "ahead", "and"),
    ("lets",),
    ("let", "us"),
    ("and",),
    ("then",),
)
_TRAILING_FILLER = (("please",), ("now",), ("thanks",), ("thank", "you"))
_JOINERS = frozenset({"and", "then"})
# Any of these anywhere means "not the fast path": "don't target the nearest hostile"
# must never match "target the nearest hostile". (normalize() turns "don't" into "dont".)
_NEGATIONS = frozenset(
    {
        *("dont", "not", "no", "never", "nope", "cant", "wont", "shouldnt"),
        *("stop", "cancel", "abort", "belay", "wait", "hold", "undo", "nevermind"),
    }
)
# Words whose difference flips or redirects a command. A fuzzy match must agree on all of them.
_MEANING_WORDS = frozenset(
    {
        *("on", "off", "up", "down", "open", "close", "shut", "lock", "unlock"),
        *("raise", "lower", "drop", "deploy", "retract"),
        *("front", "forward", "back", "rear", "left", "right", "top", "bottom"),
        *("engines", "engine", "thrusters", "shields", "shield", "weapons", "weapon", "guns"),
        *("max", "maximum", "full", "more", "reset", "next", "nearest", "closest"),
        *("min", "minimum", "less", "least"),
        *("enable", "disable", "engage", "disengage", "activate", "deactivate"),
        *("coupled", "decoupled", "couple", "decouple", "recouple"),
        *("1", "2", "3", "4", "5", "one", "two", "three", "four", "five"),
        *("first", "second", "third", "fourth", "fifth"),
        "loading",  # cargo loading is not landing
    }
)


@dataclass(frozen=True, slots=True)
class Command:
    id: str
    say: tuple[str, ...]
    actions: tuple[str, ...]
    """Game actions that do this, best first. Later ones are fallbacks, usually toggles."""
    confirm: bool = False
    setup: tuple[str, ...] = ()
    """Actions pressed first, in order, to put the game in a known mode. No fallbacks:
    each needs its own key, since a toggle could leave the wrong mode."""
    settle_seconds: float = 0.0
    """How long to wait after setup for the game to change mode."""
    requires: tuple[str, ...] = ()
    """Facts the game state must show (e.g. ``tycho.game.gamelog.QUANTUM_ROUTE``),
    or nothing is pressed."""
    accepted_risks: tuple[str, ...] = ()
    """Control contexts where the key could still do something dangerous, which the
    author has decided to accept for this command (e.g. ``"player"``, on foot)."""
    steps: tuple[Step, ...] = ()
    """A sequence: these commands in order, instead of actions of its own."""
    announced: bool = False
    """The game itself says when this is done (engines, landing gear, power), so
    Tycho doesn't talk over it."""

    @property
    def is_sequence(self) -> bool:
        return bool(self.steps)

    def expand(self) -> tuple[Step, ...]:
        """What to do, step by step: a sequence's steps, or the command itself."""
        return self.steps or (Step(self),)


@dataclass(frozen=True, slots=True)
class Step:
    command: Command
    wait_seconds: float = 0.0
    """How long to wait after the previous step (never less than the chain beat)."""


MAX_STEP_WAIT = 10.0


class CommandFileError(ValueError):
    """A commands file that can't be used, with a message for the player."""


@dataclass(frozen=True, slots=True)
class Match:
    commands: tuple[Command, ...]
    """One per part of the request, in the order spoken."""
    heard: str
    similarity: float
    """The weakest part's match, 1.0 when every part matched a phrase exactly."""


@dataclass(frozen=True, slots=True)
class Defer:
    """Not for the fast path: hand the request to the language model."""

    heard: str
    reason: str
    """"empty", "negation", "no_match" or "ambiguous"."""


class CommandMatcher:
    def __init__(
        self, commands: Iterable[Command], *, threshold: float = 0.85, margin: float = 0.03
    ) -> None:
        self.commands = tuple(commands)
        self._threshold = threshold
        self._margin = margin
        self._phrases: list[tuple[tuple[str, ...], Command]] = []
        self._exact: dict[tuple[str, ...], Command] = {}
        for command in self.commands:
            for phrase in command.say:
                words = tuple(normalize(phrase))
                if problem := _phrase_problem(words):
                    raise ValueError(f"{phrase!r} in {command.id} can never match: {problem}")
                other = self._exact.setdefault(words, command)
                if other is not command:
                    raise ValueError(f"{' '.join(words)!r} is in both {other.id} and {command.id}")
                self._phrases.append((words, command))

    @classmethod
    def default(cls, user_file: Path | None = None) -> CommandMatcher:
        """The built-in commands, plus the player's own file if there is one.

        Raises ``CommandFileError`` if the player's file can't be used; the
        built-in commands alone always load.
        """
        text = resources.files("tycho.commands").joinpath("commands.toml").read_text("utf-8")
        entries = _entries(tomllib.loads(text), "the built-in commands")
        source = "the built-in commands"
        if user_file is not None and user_file.exists():
            source = str(user_file)
            try:
                user = tomllib.loads(user_file.read_text("utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
                raise CommandFileError(f"{source}: {e}") from e
            entries = _merge(entries, _entries(user, source))
        try:
            return cls(_build(entries))
        except (KeyError, TypeError, ValueError) as e:
            raise CommandFileError(f"{source}: {e}") from e

    def get(self, command_id: str) -> Command | None:
        return next((c for c in self.commands if c.id == command_id), None)

    def match(self, text: str) -> Match | Defer:
        words = _strip_filler(normalize(text))
        if not words:
            return Defer(text, "empty")
        if _NEGATIONS.intersection(words):
            return Defer(text, "negation")
        parts = _split(words)
        found: list[Command] = []
        weakest = 1.0
        for part in parts:
            result = self._match_part(part)
            if isinstance(result, str):
                return Defer(text, result)
            command, score = result
            found.append(command)
            weakest = min(weakest, score)
        return Match(tuple(found), text, weakest)

    def _match_part(self, words: tuple[str, ...]) -> tuple[Command, float] | str:
        if (exact := self._exact.get(words)) is not None:
            return exact, 1.0
        meaning = _MEANING_WORDS.intersection(words)
        heard = "".join(words)
        # Each command's best score, whether or not it clears the threshold: a close
        # runner-up makes the winner uncertain even when the runner-up itself is too weak.
        best: dict[str, tuple[float, Command]] = {}
        for phrase, command in self._phrases:
            if _MEANING_WORDS.intersection(phrase) != meaning:
                continue
            score = similarity(heard, "".join(phrase))
            if score > best.get(command.id, (-1.0, command))[0]:
                best[command.id] = (score, command)
        ranked = sorted(best.values(), key=lambda s: -s[0])
        if not ranked or ranked[0][0] < self._threshold:
            return "no_match"
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < self._margin:
            return "ambiguous"
        return ranked[0][1], ranked[0][0]


def _phrase_problem(words: tuple[str, ...]) -> str | None:
    """Why a command phrase could never be matched, or None if it's fine."""
    if not words:
        return "it's empty"
    if _JOINERS.intersection(words):
        return 'it contains "and" or "then", which split a request into parts'
    if _NEGATIONS.intersection(words):
        return "it contains a negation, which always defers"
    if _strip_filler(list(words)) != words:
        return "it starts or ends with a filler word, which is stripped"
    return None


def _strip_filler(words: list[str]) -> tuple[str, ...]:
    changed = True
    while changed and words:
        changed = False
        for filler in _LEADING_FILLER:
            if tuple(words[: len(filler)]) == filler:
                words, changed = words[len(filler) :], True
        for filler in _TRAILING_FILLER:
            if len(words) > len(filler) and tuple(words[-len(filler) :]) == filler:
                words, changed = words[: -len(filler)], True
    return tuple(words)


def _split(words: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Split a chain on "and"/"then". A joiner at the start or end isn't a split."""
    parts: list[tuple[str, ...]] = []
    current: list[str] = []
    for word in words:
        if word in _JOINERS and current:
            parts.append(_strip_filler(current))
            current = []
        else:
            current.append(word)
    if current:
        parts.append(_strip_filler(current))
    return [p for p in parts if p]


# A commands file is a list of [[command]] and [[sequence]] tables. Entries stay
# raw tables until every file is merged, so a player's sequence can use a player's
# command, and a replaced command changes every sequence that uses it.
type _Entry = dict[str, Any]


def _entries(data: dict[str, Any], source: str) -> dict[str, _Entry]:
    entries: dict[str, _Entry] = {}
    for kind in ("command", "sequence"):
        raw_list: Any = data.get(kind, [])
        if not isinstance(raw_list, list):
            raise CommandFileError(f"{source}: [[{kind}]] must be a list of tables")
        for raw in raw_list:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(raw, dict) or "id" not in raw:
                raise CommandFileError(f"{source}: every [[{kind}]] needs an id")
            entry: _Entry = {**raw, "_kind": kind}  # pyright: ignore[reportUnknownArgumentType]
            entry_id = str(entry["id"])
            if entry_id in entries:
                raise CommandFileError(f"{source}: {entry_id!r} is defined twice")
            entries[entry_id] = entry
    return entries


def _merge(base: dict[str, _Entry], user: dict[str, _Entry]) -> dict[str, _Entry]:
    """The player's entries replace built-in ones with the same id, and take their
    phrases: a phrase the player gives an entry is removed from every built-in one."""
    claimed = {
        tuple(normalize(str(phrase))) for entry in user.values() for phrase in entry.get("say", ())
    }
    merged: dict[str, _Entry] = {}
    for entry_id, entry in base.items():
        if entry_id in user:
            continue
        say = [s for s in entry.get("say", ()) if tuple(normalize(str(s))) not in claimed]
        merged[entry_id] = {**entry, "say": say}
    return merged | user


def _build(entries: dict[str, _Entry]) -> list[Command]:
    commands = {
        entry_id: _command(entry)
        for entry_id, entry in entries.items()
        if entry["_kind"] == "command"
    }
    built: list[Command] = list(commands.values())
    for entry_id, entry in entries.items():
        if entry["_kind"] == "sequence":
            built.append(_sequence(entry_id, entry, commands, entries))
    return built


def _sequence(
    entry_id: str, entry: _Entry, commands: dict[str, Command], entries: dict[str, _Entry]
) -> Command:
    steps: list[Step] = []
    for raw in entry.get("steps", ()):
        # A step is a command id, or a table with the id and a wait.
        step = cast(dict[str, Any], raw) if isinstance(raw, dict) else {"command": raw}
        name = str(step.get("command", ""))
        if name not in commands:
            kind = (
                "a sequence (sequences can't contain sequences)" if name in entries else "unknown"
            )
            raise ValueError(f"sequence {entry_id!r}: step {name!r} is {kind}")
        wait = float(step.get("wait", 0.0))
        if not 0 <= wait <= MAX_STEP_WAIT:
            raise ValueError(f"sequence {entry_id!r}: wait {wait} s is not 0 to {MAX_STEP_WAIT} s")
        steps.append(Step(commands[name], wait))
    if not steps:
        raise ValueError(f"sequence {entry_id!r} has no steps")
    say = tuple(str(s) for s in entry.get("say", ()))
    return Command(id=entry_id, say=say, actions=(), steps=tuple(steps))


def _command(entry: _Entry) -> Command:
    return Command(
        id=str(entry["id"]),
        say=tuple(str(s) for s in entry.get("say", ())),
        actions=tuple(str(a) for a in entry["actions"]),
        confirm=bool(entry.get("confirm", False)),
        setup=tuple(str(a) for a in entry.get("setup", ())),
        settle_seconds=float(entry.get("settle_seconds", 0.0)),
        requires=tuple(str(r) for r in entry.get("requires", ())),
        accepted_risks=tuple(str(c) for c in entry.get("accepted_risks", ())),
        announced=bool(entry.get("announced", False)),
    )
