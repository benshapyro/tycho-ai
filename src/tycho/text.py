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


def name_score(said: Sequence[str], name: Sequence[str]) -> float:
    """How well the words said fit a name, for look-ups (commodities, items).

    1.0 less a little per extra word in the name when every word said is in it;
    0.9 less the same when some are only near ("quantanium") or the start of a
    word ("quant"); below 0.5 otherwise, which is never a match. A real word
    beats a near miss, so "laranite" is Laranite and not Caranite.
    """
    extra = 0.05 * max(0, len(name) - len(said))
    if all(s in name for s in said):
        return 1.0 - extra
    if all(any(_near(s, n) for n in name) for s in said):
        return 0.9 - extra
    return 0.5 * similarity(" ".join(said), " ".join(name))


def _near(said: str, word: str) -> bool:
    """A misspelling or the start of a word; never in model codes ("fs9", "c2")."""
    if said == word:
        return True
    if any(c.isdigit() for c in said + word) or min(len(said), len(word)) < 4:
        return False
    return word.startswith(said) or similarity(said, word) >= 0.8
