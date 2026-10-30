"""Word error rate, plus whether game terms survived transcription.

Plain WER treats "ArcCorp" heard as "arc corp" as two errors, but a fuzzy
matcher downstream would still get it right, while "arc court" it wouldn't.
So game terms are also checked on their own, ignoring spaces.
"""

import re
from dataclasses import dataclass

from ultimo.text import edit_distance

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
    stops "where's" and "where is" from counting as a mistake.
    """
    text = _APOSTROPHES.sub("'", text.lower())
    words = (_CONTRACTIONS.get(word, word) for word in text.split())
    text = _APOSTROPHES.sub("", " ".join(words))
    return _NON_WORD.sub(" ", text).split()


def word_errors(reference: list[str], hypothesis: list[str]) -> int:
    """Minimum substitutions, insertions and deletions to turn one word list into the other."""
    return edit_distance(reference, hypothesis)


@dataclass(frozen=True, slots=True)
class ClipScore:
    reference_words: int
    errors: int
    exact: bool
    terms_expected: tuple[str, ...]
    terms_missed: tuple[str, ...]


def score_clip(reference: str, hypothesis: str, terms: frozenset[str] = frozenset()) -> ClipScore:
    """Score one transcript. ``terms`` are game words to check, e.g. {"arccorp", "quantanium"}."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    joined = "".join(hyp)
    expected = tuple(sorted(t for t in terms if t in "".join(ref)))
    return ClipScore(
        reference_words=len(ref),
        errors=word_errors(ref, hyp),
        exact=ref == hyp,
        terms_expected=expected,
        terms_missed=tuple(t for t in expected if t not in joined),
    )
