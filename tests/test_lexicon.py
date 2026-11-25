from pathlib import Path

import pytest

from tycho.lexicon import Lexicon, sound_key
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)

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
        "who makes the Mercury Star Runner",  # was "Mercury Starlancer"
    ],
)
def test_default_lexicon_leaves_real_phrases_alone(text: str) -> None:
    assert Lexicon.default(pack=SC).correct(text).replacements == ()


def _ordinary_speech() -> list[str]:
    path = Path(__file__).parent / "fixtures" / "ordinary_speech.txt"
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


@pytest.mark.parametrize("text", _ordinary_speech())
def test_default_lexicon_leaves_ordinary_speech_alone(text: str) -> None:
    assert Lexicon.default(pack=SC).correct(text).replacements == ()


def test_a_single_sound_alike_word_is_a_hint_not_a_rewrite() -> None:
    corrected = Lexicon.default(pack=SC).correct("the station is right ahead")
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
    assert len(Lexicon.default(pack=SC)) > 100


@pytest.mark.parametrize(("heard", "term"), [("hunger", "hangar"), ("hungers", "hangars")])
def test_an_everyday_word_spelled_unlike_the_term_is_only_a_hint(heard: str, term: str) -> None:
    # #505: they share a sound key, but "I'm hungers" isn't about the hangar.
    corrected = Lexicon.default(pack=SC).correct(f"I'm {heard}")
    assert corrected.text == f"I'm {heard}"
    assert [(h.heard, h.term) for h in corrected.hints] == [(heard, term)]


@pytest.mark.parametrize(
    ("heard", "fixed"), [("open hanger", "open hangar"), ("Open hangers.", "Open hangars.")]
)
def test_a_close_spelling_of_the_term_is_still_fixed(heard: str, fixed: str) -> None:
    assert Lexicon.default(pack=SC).correct(heard).text == fixed


def test_one_word_right_at_the_spelling_cut_off_is_still_fixed() -> None:
    # #513: "kurracks" sounds exactly like "Carracks" and its spelling is exactly 0.75
    # alike, the default cut-off, which is compared with "<": it's a rewrite, not a hint.
    # A later change to the cut-off or to `similarity` must not quietly make it a hint.
    lexicon = Lexicon(["Carracks"])
    corrected = lexicon.correct("the kurracks")
    assert corrected.text == "the Carracks"
    assert [(r.heard, r.term, r.similarity) for r in corrected.replacements] == [
        ("kurracks", "Carracks", 1.0)
    ]
    # Just above the cut-off it's only a hint.
    stricter = Lexicon(["Carracks"], min_word_spelling_similarity=0.76).correct("the kurracks")
    assert stricter.text == "the kurracks"
    assert [h.heard for h in stricter.hints] == ["kurracks"]


def test_a_word_the_pack_says_a_term_is_misheard_as_is_a_hint_for_the_brain() -> None:
    # #509: "the guns on the address" is the Idris, but "address" is a real word and
    # spelled nothing like it, so it's never rewritten: the brain is told instead.
    lexicon = Lexicon.default(pack=SC)
    corrected = lexicon.correct("how do you shoot the guns on the address?")
    assert corrected.text == "how do you shoot the guns on the address?"
    assert [(h.heard, h.term) for h in corrected.misheard] == [("address", "Idris")]
    assert corrected.for_brain == (
        'how do you shoot the guns on the address?\n[Speech-to-text: "address" may be "Idris".]'
    )
    # Sound-alike hints are too common to send ("station" for Stanton, review of #522).
    station = lexicon.correct("the station is right ahead")
    assert station.hints
    assert station.for_brain == "the station is right ahead"
    # A real address is left as it is too; only the brain sees the hint.
    assert lexicon.correct("what's the address of that shop").text.endswith("that shop")
    # Nothing to tell: the brain gets the words alone.
    assert lexicon.correct("landing gear").for_brain == "landing gear"


def test_misheard_words_match_whole_words_only() -> None:
    lexicon = Lexicon(["Idris"], misheard=[("address", "Idris")])
    assert lexicon.correct("the addresses").misheard == ()
    assert [h.heard for h in lexicon.correct("The Address").misheard] == ["Address"]


@pytest.mark.parametrize(
    ("heard", "term"),
    [
        ("we're in Nix", "Nyx"),
        ("I'm in Nick's", "Nyx"),
        ("Nicks system", "Nyx"),
        ("in Piro", "Pyro"),
    ],
)
def test_a_system_misheard_is_named_to_the_brain(heard: str, term: str) -> None:
    # #656: speech-to-text heard Nyx as "Nick's" or "Nix", too short to sound alike.
    corrected = Lexicon.default(pack=SC).correct(heard)
    assert corrected.text == heard  # "Nick's" may be a real name: never rewritten
    assert f'may be "{term}"' in corrected.for_brain
