"""The confirm question's answers, apart from the session (#399): "confirm", "cancel"
and "say that again", each with the question open, expired, or gone."""

from tycho.commands import Command
from tycho.runtime.asking import Answer, Question, answered, say_again
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
