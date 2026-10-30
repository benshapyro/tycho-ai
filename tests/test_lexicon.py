from pathlib import Path

import pytest

from tycho.lexicon import Lexicon, sound_key

LEXICON = Lexicon(["ArcCorp", "mobiGlas", "Cutlass", "QT", "Ita", "Hurston", "quantum"])


def test_sound_key_merges_spelling_variants() -> None:
    assert sound_key("Moby Glass") == sound_key("mobiGlas")


@pytest.mark.parametrize(
    ("heard", "fixed"),
    [
        ("QT to Art Corp.", "QT to ArcCorp."),
        ("Open Moby Glass", "Open mobiGlas"),
        ("Is my cut list loadout any good?", "Is my Cutlass loadout any good?"),
        ("Q T to ArcCorp", "QT to ArcCorp"),
        ("how long is the jump to hurston", "how long is the jump to Hurston"),
    ],
)
def test_fixes_misheard_terms(heard: str, fixed: str) -> None:
    assert LEXICON.correct(heard).text == fixed


@pytest.mark.parametrize(
    "text",
    [
        "lower the landing gear",
        "is it a good idea",  # "it a" must not become the moon Ita
        "spool the quantum drive",
        "max power to weapons",
    ],
)
def test_leaves_ordinary_speech_alone(text: str) -> None:
    corrected = LEXICON.correct(text)
    assert corrected.text == text
    assert corrected.replacements == ()


@pytest.mark.parametrize(
    "text",
    [
        # False alarms the benchmark found with the default lexicon.
        "get us ready to leave and call the tower",  # was "Riccite"
        "set a route to microTech",  # was "set Ouratite microTech"
        "spool the quantum drive",  # was "quantum Drake"
    ],
)
def test_default_lexicon_leaves_real_phrases_alone(text: str) -> None:
    assert Lexicon.default().correct(text).replacements == ()


def _ordinary_speech() -> list[str]:
    path = Path(__file__).parent / "fixtures" / "ordinary_speech.txt"
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


@pytest.mark.parametrize("text", _ordinary_speech())
def test_default_lexicon_leaves_ordinary_speech_alone(text: str) -> None:
    assert Lexicon.default().correct(text).replacements == ()


def test_a_single_sound_alike_word_is_a_hint_not_a_rewrite() -> None:
    corrected = Lexicon.default().correct("the station is right ahead")
    assert corrected.text == "the station is right ahead"
    assert [(h.heard, h.term) for h in corrected.hints] == [("station", "Stanton")]


def test_does_not_swallow_neighbouring_words() -> None:
    assert LEXICON.correct("set course to Art Corp now").text == "set course to ArcCorp now"


def test_reports_what_it_changed() -> None:
    (replacement,) = LEXICON.correct("Open Moby Glass").replacements
    assert (replacement.heard, replacement.term) == ("Moby Glass", "mobiGlas")
    assert replacement.similarity == 1.0


def test_case_only_changes_are_not_reported() -> None:
    assert LEXICON.correct("arccorp").replacements == ()


def test_default_lexicon_loads() -> None:
    assert len(Lexicon.default()) > 100
