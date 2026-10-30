"""Word error rate, plus whether game terms survived transcription.

Plain WER treats "ArcCorp" heard as "arc corp" as two errors, but a fuzzy
matcher downstream would still get it right, while "arc court" it wouldn't.
So game terms are also checked on their own, ignoring spaces.
"""

import re
from dataclasses import dataclass

_APOSTROPHES = re.compile("['\N{RIGHT SINGLE QUOTATION MARK}]")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> list[str]:
    """Lowercase words with punctuation removed. "We're done, QT!" -> ["were", "done", "qt"]."""
    text = _APOSTROPHES.sub("", text.lower())
    return _NON_WORD.sub(" ", text).split()


def word_errors(reference: list[str], hypothesis: list[str]) -> int:
    """Minimum substitutions, insertions and deletions to turn one word list into the other."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref_word in enumerate(reference, start=1):
        current = [i]
        for j, hyp_word in enumerate(hypothesis, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ref_word != hyp_word),
                )
            )
        previous = current
    return previous[-1]


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
