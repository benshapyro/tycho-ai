"""Small text utilities shared by speech correction, command matching and evaluation."""

import re
from collections.abc import Hashable, Sequence

_CONTRACTIONS = {
    "what's": "what is",
    "where's": "where is",
    "how's": "how is",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "we're": "we are",
    "you're": "you are",
    "they're": "they are",
    "i'm": "i am",
    "let's": "let us",
}
_APOSTROPHES = re.compile("['\N{RIGHT SINGLE QUOTATION MARK}]")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> list[str]:
    """Lowercase words with punctuation removed and common contractions expanded.

    "We're done, QT!" -> ["we", "are", "done", "qt"]. Expanding contractions
    stops "where's" and "where is" from counting as different.
    """
    text = _APOSTROPHES.sub("'", text.lower())
    words = (_CONTRACTIONS.get(word, word) for word in text.split())
    text = _APOSTROPHES.sub("", " ".join(words))
    return _NON_WORD.sub(" ", text).split()


def edit_distance(a: Sequence[Hashable], b: Sequence[Hashable]) -> int:
    """Levenshtein distance between two sequences: characters, or words."""
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (item_a != item_b),
                )
            )
        previous = current
    return previous[-1]


def similarity(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """1.0 for identical sequences, falling towards 0 as they differ."""
    return 1 - edit_distance(a, b) / max(len(a), len(b), 1)
