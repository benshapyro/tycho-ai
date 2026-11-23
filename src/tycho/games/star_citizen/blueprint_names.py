"""Matching a blueprint, mission or giver name as said (#596, #600): placeholders like
"[TargetName]" are left out, and numbers match as words, digits or Roman numerals."""

import re
from collections.abc import Mapping

from tycho.text import name_score, normalize

MATCH = 0.8
"""The lowest ``name_score`` that counts as the name said (0.9 is a near miss)."""
CLOSEST = 3


def words(name: str) -> list[str]:
    """A name's words for matching, without the game's placeholders ("[Location]")."""
    return normalize(re.sub(r"\[[^\]]*\]", " ", name))


NUMBERS = {
    word: (str(n), roman)
    for n, (word, roman) in enumerate(
        [
            ("one", "i"),
            ("two", "ii"),
            ("three", "iii"),
            ("four", "iv"),
            ("five", "v"),
            ("six", "vi"),
            ("seven", "vii"),
            ("eight", "viii"),
            ("nine", "ix"),
            ("ten", "x"),
            ("eleven", "xi"),
            ("twelve", "xii"),
            ("thirteen", "xiii"),
            ("fourteen", "xiv"),
            ("fifteen", "xv"),
            ("sixteen", "xvi"),
            ("seventeen", "xvii"),
            ("eighteen", "xviii"),
            ("nineteen", "xix"),
            ("twenty", "xx"),
        ],
        start=1,
    )
}
"""Number words, with their digits and Roman numerals: blueprint names use all three
("Omnisky III", "Klein-S1"), and speech gives words or digits (#600)."""
_AS_DIGIT = {
    form: digit for word, (digit, roman) in NUMBERS.items() for form in (word, digit, roman)
}
_ROMAN = {digit: roman for digit, roman in NUMBERS.values()}


def as_digits(text: str) -> str:
    """``text``'s words with every number as digits: "Omnisky Three" is "omnisky 3"."""
    return " ".join(_AS_DIGIT.get(w, w) for w in normalize(text))


def spellings(words: list[str]) -> list[list[str]]:
    """The words as said, with numbers as digits, and as Roman numerals."""
    digits = [_AS_DIGIT.get(w, w) for w in words]
    romans = [_ROMAN.get(w, w) for w in digits]
    return [list(f) for f in dict.fromkeys(tuple(f) for f in (words, digits, romans))]


def score(said: str, name: str) -> float:
    heard = words(said)
    if not heard:
        return 0.0
    named = spellings(words(name))  # a name can spell its number as a word too (#602)
    return max(name_score(form, n) for form in spellings(heard) for n in named)


def ranked(said: str, names: Mapping[str, object]) -> list[str]:
    """The names that fit what was said, best first."""
    scored = [(s, n) for n in names if (s := score(said, n)) >= MATCH]
    scored.sort(key=lambda p: (-p[0], len(p[1])))
    return [n for _, n in scored]


def closest(said: str, names: Mapping[str, object]) -> list[str]:
    scored = sorted(names, key=lambda n: -score(said, n))
    return scored[:CLOSEST]
