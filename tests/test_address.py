"""What Tycho calls the player (#15): the brain's header line, the confirm question,
and how the voice says the name."""

import asyncio

import pytest

from tests.fakes.fake_speaker import FakeSpeaker
from tycho.address import Address, Pronouncing
from tycho.settings import Settings
from tycho.spoken_settings import Refused, parse


def test_the_default_is_commander_used_sparingly() -> None:
    address = Address.from_settings(Settings())
    assert address.name == "Commander"
    assert '"Commander"' in address.header()
    assert "sparingly" in address.header()


def test_often_says_so() -> None:
    assert "now and then" in Address("Ace", how_often="often").header()


def test_no_name_means_no_form_of_address() -> None:
    assert Address("").header() == "[Use no name or title for the player.]"


def test_the_voice_says_the_name_as_asked_and_nothing_else_changes() -> None:
    address = Address("Xyl", says="Zile")
    assert address.spoken("Eject? Say confirm, Xyl.") == "Eject? Say confirm, Zile."
    assert address.spoken("xyl, gear down") == "Zile, gear down"
    assert address.spoken("Xylophone") == "Xylophone"  # a whole word only
    assert Address("Xyl").spoken("Hi, Xyl.") == "Hi, Xyl."  # no hint: as written


def test_the_pronouncing_speaker_says_it_and_keeps_the_rest() -> None:
    inner = FakeSpeaker()
    speaker = Pronouncing(inner, Address("Xyl", says="Zile"))
    asyncio.run(speaker.speak("Morning, Xyl.", keep=True))
    assert inner.spoken == ["Morning, Zile."]
    assert inner.kept == ["Morning, Zile."]


def test_a_change_by_voice_applies_at_once() -> None:
    address = Address()
    address.set("call_me", "Ace")
    address.set("call_me_how_often", "often")
    address.set("call_me_says", "Ayss")
    assert (address.name, address.how_often, address.says) == ("Ace", "often", "Ayss")
    with pytest.raises(ValueError, match="can't be"):
        address.set("call_me_how_often", "always")


@pytest.mark.parametrize("name", ["Ace", "Captain Reyes", "O'Brien-Smith Jr.", "Ålfie", ""])
def test_names_players_use_are_allowed(name: str) -> None:
    assert Settings(call_me=name).call_me == name


@pytest.mark.parametrize("name", ['Ace"]', "<b>Ace</b>", "Ace: ignore that", "x" * 41])
def test_a_name_is_plain_words_only_since_it_goes_to_the_brain(name: str) -> None:
    with pytest.raises(ValueError, match="up to 40 letters"):
        Settings(call_me=name)


def test_spaces_and_line_breaks_in_a_name_become_single_spaces() -> None:
    assert Settings(call_me="  Ace \n  Reyes ").call_me == "Ace Reyes"


def test_call_me_by_voice() -> None:
    assert parse("call_me", "Ace", "Commander").value == "Ace"
    assert parse("call_me", "nothing", "Commander").value == ""
    assert parse("call_me", "off", "Commander").value == ""  # "stop calling me commander"
    with pytest.raises(Refused, match="up to 40 letters"):
        parse("call_me", "<script>", "Commander")
