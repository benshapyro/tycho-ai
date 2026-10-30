import pytest

from tycho.eval import normalize, score_clip, word_errors


def test_normalize_drops_case_punctuation_and_apostrophes() -> None:
    assert normalize("Done here, Cutlass' power... DOWN!") == [
        "done",
        "here",
        "cutlass",
        "power",
        "down",
    ]


def test_normalize_expands_contractions() -> None:
    assert normalize("Where\N{RIGHT SINGLE QUOTATION MARK}s the pad? We're late") == normalize(
        "where is the pad we are late"
    )


@pytest.mark.parametrize(
    ("ref", "hyp", "errors"),
    [
        ("a b c", "a b c", 0),
        ("a b c", "a x c", 1),
        ("a b c", "a c", 1),
        ("a b c", "a b c d", 1),
        ("a b c", "", 3),
        ("", "a", 1),
    ],
)
def test_word_errors(ref: str, hyp: str, errors: int) -> None:
    assert word_errors(ref.split(), hyp.split()) == errors


def test_exact_match_ignores_formatting() -> None:
    score = score_clip("QT to ArcCorp", "qt to arccorp.")
    assert score.exact
    assert score.errors == 0


def test_term_survives_being_split_into_two_words() -> None:
    score = score_clip("QT to ArcCorp", "QT to Arc Corp", frozenset({"arccorp"}))
    assert score.errors == 2
    assert score.terms_expected == ("arccorp",)
    assert score.terms_missed == ()


def test_misheard_term_is_missed() -> None:
    score = score_clip("QT to ArcCorp", "cutie to arc court", frozenset({"arccorp", "qt"}))
    assert score.terms_missed == ("arccorp", "qt")


def test_terms_not_in_the_reference_are_ignored() -> None:
    score = score_clip("lights on", "lights on", frozenset({"arccorp"}))
    assert score.terms_expected == ()
