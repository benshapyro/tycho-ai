"""Fixing game words in transcripts by how they sound.

Speech-to-text models hear "ArcCorp" as "Art Corp" and "Cutlass" as "cut list".
The corrector compares runs of up to three transcribed words against known game
terms, using a rough sound key where vowels and look-alike consonants collapse
together, and swaps in the term when the match is close enough.

It rewrites only when confident. A single word that merely sounds like a term
might be a real word ("station" or Stanton, "horizon" or Orison), and only
context can tell, so those come back as hints for the language model instead.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources

from tycho.text import similarity

_WORD = re.compile(r"[A-Za-z0-9']+")
_NOT_ALNUM = re.compile(r"[^a-z0-9]")
_VOWEL_RUN = re.compile(r"[aeiou]+")
_REPEATS = re.compile(r"(.)\1+")
_SPELLINGS = (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"))
_LETTERS = str.maketrans("cqzy", "kksi")
# A game term never starts or ends with one of these, so a multi-word match mustn't either.
_FILLER = frozenset(
    [
        "a",
        "an",
        "the",
        "to",
        "of",
        "and",
        "or",
        "in",
        "on",
        "at",
        "for",
        "from",
        "is",
        "are",
        "my",
        "our",
        "your",
        "us",
        "me",
        "it",
        "this",
        "that",
    ]
)


def _is_hint(start: int, end: int, similarity: float) -> bool:
    """A single word that only sounds like a term is a hint, never a rewrite."""
    return end - start == 1 and similarity < 1.0


def _collapse(text: str) -> str:
    return _NOT_ALNUM.sub("", text.lower())


def sound_key(text: str) -> str:
    """A rough key for how a word sounds: "Moby Glass" and "mobiGlas" share one."""
    key = _collapse(text)
    for spelling, sound in _SPELLINGS:
        key = key.replace(spelling, sound)
    key = key.translate(_LETTERS)
    key = _VOWEL_RUN.sub("a", key)
    return _REPEATS.sub(r"\1", key)


@dataclass(frozen=True, slots=True)
class Replacement:
    heard: str
    term: str
    similarity: float


@dataclass(frozen=True, slots=True)
class Corrected:
    text: str
    replacements: tuple[Replacement, ...]
    hints: tuple[Replacement, ...] = ()
    """Possible terms left in place, e.g. "station" might be Stanton."""


@dataclass(frozen=True, slots=True)
class _Term:
    spelling: str
    collapsed: str
    key: str


class Lexicon:
    """Known game terms, and the corrector that uses them."""

    def __init__(
        self,
        terms: Iterable[str],
        *,
        threshold: float = 0.8,
        min_spelling_similarity: float = 0.65,
        min_fuzzy_length: int = 5,
        max_words: int = 3,
    ) -> None:
        self._terms = [_Term(t, _collapse(t), sound_key(t)) for t in terms]
        self._threshold = threshold
        self._min_spelling_similarity = min_spelling_similarity
        self._min_fuzzy_length = min_fuzzy_length
        self._max_words = max_words

    @classmethod
    def default(cls) -> Lexicon:
        text = resources.files("tycho.lexicon").joinpath("terms.txt").read_text(encoding="utf-8")
        lines = (line.strip() for line in text.splitlines())
        return cls(line for line in lines if line and not line.startswith("#"))

    def __len__(self) -> int:
        return len(self._terms)

    def correct(self, text: str) -> Corrected:
        words = list(_WORD.finditer(text))
        candidates: list[tuple[float, int, int, int, _Term]] = []
        for start in range(len(words)):
            for end in range(start + 1, min(start + self._max_words, len(words)) + 1):
                heard = [w.group() for w in words[start:end]]
                match = self._match(heard)
                if match is not None:
                    term, similarity = match
                    candidates.append((similarity, -(end - start), start, end, term))

        # Rewrites claim words before hints (a hint must never block a fixable term),
        # then best match first; on a tie, the longer one, so "quantum drive" beats "quantum".
        def rank(c: tuple[float, int, int, int, _Term]) -> tuple[bool, float, int]:
            similarity, negative_length, start, end, _ = c
            return (_is_hint(start, end, similarity), -similarity, negative_length)

        chosen: list[tuple[int, int, _Term, float]] = []
        taken: set[int] = set()
        for similarity, _, start, end, term in sorted(candidates, key=rank):
            span = set(range(start, end))
            if not span & taken:
                taken |= span
                chosen.append((start, end, term, similarity))

        out: list[str] = []
        replacements: list[Replacement] = []
        hints: list[Replacement] = []
        cursor = 0
        for start, end, term, similarity in sorted(chosen, key=lambda c: c[0]):
            begin, finish = words[start].start(), words[end - 1].end()
            heard = text[begin:finish]
            change = Replacement(heard, term.spelling, round(similarity, 3))
            if _is_hint(start, end, similarity):
                hints.append(change)
                continue
            out += [text[cursor:begin], term.spelling]
            cursor = finish
            if heard.lower() != term.spelling.lower():
                replacements.append(change)
        out.append(text[cursor:])
        return Corrected("".join(out), tuple(replacements), tuple(hints))

    def _match(self, heard: list[str]) -> tuple[_Term, float] | None:
        """The closest term to these words, if close enough."""
        collapsed = _collapse("".join(heard))
        key = sound_key(collapsed)
        spelled_out = all(len(w) == 1 for w in heard)  # "Q T" for QT
        edges_are_filler = len(heard) > 1 and bool({heard[0].lower(), heard[-1].lower()} & _FILLER)
        best: tuple[_Term, float] | None = None
        for term in self._terms:
            long_enough = len(term.collapsed) >= self._min_fuzzy_length
            if collapsed == term.collapsed:
                # Joining words only for long terms or spelled-out letters,
                # so "it a" never becomes the moon Ita.
                if len(heard) == 1 or spelled_out or long_enough:
                    return term, 1.0
                continue
            if not long_enough or len(key) < self._min_fuzzy_length or edges_are_filler:
                continue
            if abs(len(key) - len(term.key)) > 2:
                continue
            sounds = similarity(key, term.key)
            if sounds < self._threshold or (best is not None and sounds <= best[1]):
                continue
            # Sounding alike isn't enough on its own: "ready to" and Riccite do.
            if similarity(collapsed, term.collapsed) >= self._min_spelling_similarity:
                best = (term, sounds)
        return best
