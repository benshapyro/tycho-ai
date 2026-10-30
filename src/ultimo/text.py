"""Small text utilities shared by speech correction and evaluation."""

from collections.abc import Hashable, Sequence


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
