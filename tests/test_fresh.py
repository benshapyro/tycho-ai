"""Answers kept for a while (knowledge/fresh.py)."""

from tycho.knowledge.fresh import Fresh


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_an_answer_is_used_until_it_expires() -> None:
    clock = Clock()
    fresh: Fresh[str] = Fresh(600, clock)
    fresh.keep("a", "answer")
    clock.now = 599
    assert fresh.get("a") == "answer"
    clock.now = 600
    assert fresh.get("a") is None


def test_expired_answers_are_dropped_not_kept_for_ever() -> None:
    # A long session asked about hundreds of things and kept them all (audit, 2026-11-03).
    clock = Clock()
    fresh: Fresh[int] = Fresh(600, clock)
    for n in range(500):
        clock.now = n * 10.0  # one question every 10 s
        fresh.keep(f"q{n}", n)
    assert len(fresh) == 60  # only the last ten minutes
    assert fresh.get("q499") == 499
