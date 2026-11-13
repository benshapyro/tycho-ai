"""Cutting a spoken line to the voice's limit (#350)."""

from tycho.text import clip_spoken


def test_a_line_that_fits_is_left_alone() -> None:
    assert clip_spoken("Short and sweet", 40) == "Short and sweet"


def test_a_long_line_ends_at_its_last_whole_sentence() -> None:
    line = "First part. Second part! Third part goes on and on"
    assert clip_spoken(line, 30) == "First part. Second part!"


def test_a_sentence_end_right_at_the_limit_counts() -> None:
    assert clip_spoken("Twelve chars. More", 13) == "Twelve chars."


def test_without_an_early_enough_sentence_end_it_cuts_at_a_word() -> None:
    line = "Hi. " + "a long run of words with no stop at all " * 3
    clipped = clip_spoken(line, 60)
    assert len(clipped) <= 60
    assert clipped.endswith(".")
    assert not clipped.endswith(" .")
    assert line.startswith(clipped[:-1])


def test_a_decimal_point_isnt_a_sentence_end() -> None:
    line = "It costs 2.5 aUEC per unit, more at Lorville, and less anywhere near Area18 today"
    assert clip_spoken(line, 40) == "It costs 2.5 aUEC per unit, more at."


def test_one_long_word_is_cut_inside_it() -> None:
    assert clip_spoken("x" * 50, 10) == "x" * 9 + "."


def test_a_closing_quote_stays_with_its_sentence() -> None:
    """The review of #382: a cut after "go now." dropped the quote that closed it."""
    line = 'He said "go now." Then he left without another word to anyone there'
    assert clip_spoken(line, 30) == 'He said "go now."'
