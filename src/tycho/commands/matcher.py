"""The fast path: recognising common commands on the player's PC, without the language model.

Rules, in order of importance:
1. A wrong action is worse than a slow one. Anything uncertain is deferred to
   the language model, which sees context the matcher can't.
2. A near match never flips meaning: "lights off" can't match "lights on".
3. A chain ("drop the gear and ask for a landing pad") runs on the fast path
   only if every part matches; otherwise the whole request is deferred.
"""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from typing import Any

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
    def default(cls) -> CommandMatcher:
        text = resources.files("tycho.commands").joinpath("commands.toml").read_text("utf-8")
        return cls(_parse(tomllib.loads(text)))

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


def _parse(data: dict[str, Any]) -> list[Command]:
    commands: list[Command] = []
    for raw in data.get("command", []):
        entry: dict[str, Any] = raw
        commands.append(
            Command(
                id=str(entry["id"]),
                say=tuple(str(s) for s in entry["say"]),
                actions=tuple(str(a) for a in entry["actions"]),
                confirm=bool(entry.get("confirm", False)),
            )
        )
    return commands
