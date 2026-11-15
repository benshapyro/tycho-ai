"""Asking, apart from the session (#399): the brain's time (#150, #169, #314), and the
confirm question's answers ("confirm", "cancel" and "say that again", the question
open, expired, or gone)."""

import asyncio
from collections.abc import Callable

from tycho.brain import BrainRequest, Reply
from tycho.commands import Command, CommandMatcher
from tycho.keybinds import Keybinds
from tycho.packs import STAR_CITIZEN, built_in
from tycho.runtime.asking import Answer, Question, Times, answered, ask_brain, say_again
from tycho.runtime.conversation import Line

EJECT = Command("eject", ("eject",), ("v_eject",), confirm=True)
OPEN = Question(7, (EJECT,), deadline=10.0, by_brain=True, say="Ejecting.")


def test_confirm_goes_ahead_with_the_open_question() -> None:
    assert answered("confirm", OPEN, now=9.0) is OPEN


def test_cancel_ends_the_question_and_names_it() -> None:
    assert answered("cancel", OPEN, now=9.0) == Answer(
        "done", "cancelled #7 (eject)", commands=("cancel",)
    )


def test_an_expired_or_missing_question_is_confirmed_by_nothing() -> None:
    assert answered("confirm", OPEN, now=11.0) == Answer("not_understood", "the question expired")
    assert answered("confirm", None, now=0.0) == Answer("not_understood", "nothing to confirm")
    # "Never mind" with nothing to cancel is still heard (#351).
    assert answered("cancel", None, now=0.0) == Answer(
        "done", "nothing to cancel", commands=("cancel",)
    )


def test_say_that_again_asks_the_open_question_again_and_waits_afresh() -> None:
    again = say_again(Line("Eject? Say confirm.", question=7), OPEN, now=9.0, confirm_seconds=5)
    assert again.status == "asking"
    assert again.question == "Eject? Say confirm."
    assert again.reopened == Question(7, (EJECT,), 14.0, True, "Ejecting.")


def test_say_that_again_never_reopens_an_ended_question() -> None:
    line = Line("Eject? Say confirm.", question=7)
    assert say_again(line, None, 9.0, 5).detail == "the question has ended"
    assert say_again(line, OPEN, 11.0, 5).detail == "the question expired"
    assert say_again(line, OPEN, 11.0, 5).reopened is None


def test_say_that_again_repeats_the_last_line() -> None:
    assert say_again(Line("Gear down."), None, 0.0, 5) == Answer(
        "done", "", ("say_again",), say="Gear down."
    )
    assert say_again(Line(), None, 0.0, 5).detail == "nothing to say again"


class _Brain:
    """Starts a web search or a look-up (or neither), then takes ``seconds``."""

    def __init__(self, seconds: float, start: str = "") -> None:
        self.seconds, self.start = seconds, start

    async def decide(self, request: BrainRequest) -> Reply:
        if self.start == "search" and request.waiting is not None:
            request.waiting()
        if self.start == "look" and request.looking is not None:
            request.looking()
        await asyncio.sleep(self.seconds)
        return Reply("Here.")

    async def warm(self, keybinds: Keybinds) -> None:
        return None

    async def close(self) -> None:
        return None


def _ask(brain: _Brain, times: Times, searching: Callable[[], None] = lambda: None) -> object:
    pack = built_in(STAR_CITIZEN)
    return asyncio.run(
        ask_brain(
            brain,  # pyright: ignore[reportArgumentType]
            "where's the best laranite price",
            Keybinds([]),
            (),
            "",
            times=times,
            matcher=CommandMatcher.default(pack=pack),
            lists=pack.safety,
            searching=searching,
            checking=lambda: None,
        )
    )


def test_the_brain_answers_within_its_time_or_it_took_too_long() -> None:
    assert _ask(_Brain(0.01), Times(brain=0.5)) == Reply("Here.")
    assert _ask(_Brain(0.3), Times(brain=0.05)) == "it took too long"


def test_a_web_search_moves_the_time_on_and_says_a_line() -> None:
    said: list[str] = []
    times = Times(brain=0.05, search=0.5, search_limit=1.0)
    assert _ask(_Brain(0.2, "search"), times, lambda: said.append("filler")) == Reply("Here.")
    assert said == ["filler"]


def test_a_search_never_runs_past_its_limit() -> None:
    # #169: the limit counts from the request, however late the search starts.
    times = Times(brain=0.05, search=1.0, search_limit=0.1)
    assert _ask(_Brain(0.4, "search"), times) == "it took too long"


def test_a_look_up_moves_the_time_on_up_to_its_limit() -> None:
    assert _ask(_Brain(0.15, "look"), Times(brain=0.05, looking=0.4)) == Reply("Here.")
    capped = Times(brain=0.05, looking=1.0, looking_limit=0.1)
    assert _ask(_Brain(0.4, "look"), capped) == "it took too long"
