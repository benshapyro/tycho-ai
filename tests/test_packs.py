"""Game packs: the loader, Star Citizen as the first pack, and a fake second game (ADR 18)."""

import hashlib
import re
import shutil
from pathlib import Path

import pytest

from tests.fakes.fake_keys import FakeClock, FakeFocus, FakeKeySender
from tests.fakes.fake_pack import FOLDER, REGISTRY, FakeGameKeybinds, fake_pack
from tycho.brain.briefing import (
    bound_actions,
    briefing,
    core_sections,
    game_facts,
    instructions,
)
from tycho.commands import CommandMatcher, Match
from tycho.input import Presser, code_for
from tycho.knowledge.web import SITES
from tycho.lexicon import Lexicon
from tycho.packs import (
    BUILT_IN,
    CORE_SECTIONS,
    STAR_CITIZEN,
    PackError,
    built_in,
    installed,
    load_pack,
)
from tycho.runtime.planning import Steps, plan_request
from tycho.tile.design import MAKER_ACCENTS, accent_for, accent_for_maker

STAR_CITIZEN_INSTRUCTIONS = "58cee8ce86e5f0871be300a6e735a1ae05a71563e17244a126897073d284329f"
"""The SHA-256 of Star Citizen's fixed briefing text, as it was before packs (main at
6645025). The prompt cache and the eval's scores depend on it staying byte for byte the
same. If you change the text on purpose, update this, and expect the cache to refill."""


# --- Star Citizen, the first pack --------------------------------------------------------


def test_star_citizens_briefing_is_byte_for_byte_what_it_was_before_packs() -> None:
    text = instructions(built_in(STAR_CITIZEN))
    assert hashlib.sha256(text.encode()).hexdigest() == STAR_CITIZEN_INSTRUCTIONS
    assert instructions() == text  # no pack named: Star Citizen


def test_star_citizen_is_the_pack_tycho_ships() -> None:
    pack = built_in()
    assert installed() == ("star-citizen",)
    assert (pack.id, pack.name, pack.version, pack.format) == (
        "star-citizen",
        "Star Citizen",
        "1.0.0",
        1,
    )
    assert (pack.mode, pack.window, pack.built_in) == ("keys", "StarCitizen.exe", True)
    assert pack.parts.keybinds == "star-citizen"
    assert set(pack.parts.lookups) == BUILT_IN.lookups
    assert pack.commands is not None
    assert "[[sequence]]" in pack.commands


def test_star_citizens_data_reaches_the_parts_that_used_it() -> None:
    pack = built_in()
    assert pack.web_sites == SITES
    assert "robertsspaceindustries.com" in SITES
    assert pack.tile.accents == MAKER_ACCENTS
    assert accent_for("DRAK_Cutlass_Black") == "#f5901e"
    assert accent_for_maker("Drake Interplanetary") == "#f5901e"
    assert game_facts() == pack.facts
    assert pack.facts
    assert len(Lexicon.default()) == len(pack.terms) > 100
    assert CommandMatcher.default().get("landing_gear") is not None


def test_the_core_of_the_briefing_has_every_section_a_pack_must_place() -> None:
    assert tuple(core_sections()) == CORE_SECTIONS


# --- Fake Game: a second game through the same seam -------------------------------------


def test_a_second_game_loads_with_its_own_parts() -> None:
    pack = fake_pack()
    assert (pack.id, pack.mode, pack.window, pack.built_in) == (
        "fake-game",
        "keys",
        "FakeGame.exe",
        False,
    )
    assert pack.web_sites == ("fakegame.example",)
    assert pack.terms == ("Glimmerhold", "Quillback")


def test_a_pack_naming_a_part_tycho_doesnt_have_is_refused() -> None:
    with pytest.raises(PackError, match="no built-in keybinds part 'fake-game'"):
        load_pack(FOLDER, built_in=False, registry=BUILT_IN)


def test_the_seam_presses_keys_for_a_game_that_isnt_star_citizen() -> None:
    pack = fake_pack()
    keybinds = FakeGameKeybinds().keybinds
    matcher = CommandMatcher.default(pack=pack)
    assert CommandMatcher.default(pack=pack).get("landing_gear") is None  # none of Star Citizen's
    found = matcher.match("get ready")
    assert isinstance(found, Match)
    planned = plan_request(found.commands, keybinds)
    assert isinstance(planned, Steps)
    assert planned.actions == ("shield_raise", "hatch_open")

    sender, clock = FakeKeySender(), FakeClock()
    presser = Presser(sender, FakeFocus(True), sleep=clock.sleep, clock=clock)
    for _, press in planned.presses:
        assert presser.press(press).done
    downs = [code for event, code in sender.events if event == "down"]
    assert downs == [code_for("g"), code_for("lctrl"), code_for("h")]
    assert not sender.held()


def test_a_second_games_briefing_has_the_core_rules_and_none_of_star_citizen() -> None:
    pack = fake_pack()
    keybinds = FakeGameKeybinds().keybinds
    text = briefing(keybinds, CommandMatcher.default(pack=pack), pack)
    assert text.startswith("You are Tycho, a voice helper in Fake Game")
    assert core_sections()["safety"] in text
    assert core_sections()["squad"] in text
    # The core's examples are still Star Citizen's ("Gear down", quantum in the safety
    # rule): rewording them would change Star Citizen's briefing, so that waits for a
    # second real game. The game itself isn't named.
    assert "Star Citizen" not in text
    assert "UEX" not in text
    assert "the hatch opens from inside only" in text
    # Its own filters: the photo mode map and held walking aren't offered.
    offered = bound_actions(keybinds, pack=pack)
    assert "shield_raise" in offered
    assert "photo_take" not in offered
    assert "walk_forward" not in offered


def test_a_second_games_words_reach_the_speech_corrector() -> None:
    corrected = Lexicon.default(fake_pack()).correct("fly to glimmer hold")
    assert "Glimmerhold" in corrected.text


# --- What the loader refuses --------------------------------------------------------------


def _copy(tmp_path: Path) -> Path:
    folder = tmp_path / "fake-game"
    shutil.copytree(Path(str(FOLDER)), folder)
    return folder


def _edit(folder: Path, name: str, old: str, new: str) -> None:
    file = folder / name
    text = file.read_text("utf-8")
    assert old in text
    file.write_text(text.replace(old, new, 1), "utf-8")


@pytest.mark.parametrize(
    ("name", "old", "new", "message"),
    [
        ("pack.toml", "format = 1", "format = 2", "format 2 isn't one this Tycho reads"),
        ("pack.toml", 'id = "fake-game"', 'id = "Fake Game"', "lower-case letters"),
        ("pack.toml", 'version = "0.1.0"', 'version = "new"', "must be like 1.0.0"),
        ("pack.toml", 'mode = "keys"', 'mode = "both"', 'mode must be "keys" or "advisor"'),
        ("pack.toml", 'window = "FakeGame.exe"\n', "", "needs a keybinds part and a window"),
        ("pack.toml", 'mode = "keys"', 'mode = "advisor"', "an advisor pack can't have"),
        ("pack.toml", "[parts]", 'colour = "red"\n[parts]', "unknown settings: colour"),
        ("pack.toml", 'lookups = ["settings"]', 'lookups = ["horoscope"]', "no built-in look-up"),
        ("pack.toml", "fakegame.example", "https://fakegame.example/", "bare domains"),
        ("pack.toml", "'''walk_\\w+'''", "'''walk_(\\w+'''", "isn't a pattern"),
        ("briefing.md", "{{core:safety}}\n", "", "must all be placed; missing safety"),
        ("briefing.md", "{{core:tools}}", "{{core:tools}}\n\n{{core:tools}}", "more than once"),
        ("briefing.md", "{{core:tools}}", "{{core:rules}}", "no core section rules"),
        ("briefing.md", "{{core:voice}}", "Talk {{core:voice}}", "a line of its own"),
        ("commands.toml", "[[command]]", "[[command]", "commands.toml"),
    ],
)
def test_a_pack_that_isnt_right_is_refused_with_why(
    tmp_path: Path, name: str, old: str, new: str, message: str
) -> None:
    folder = _copy(tmp_path)
    _edit(folder, name, old, new)
    with pytest.raises(PackError, match=re.escape(message)):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_a_pack_holds_only_the_files_a_pack_can(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "reader.py").write_text("print('hi')", "utf-8")
    with pytest.raises(PackError, match=re.escape("files a pack can't hold: reader.py")):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_a_tile_accent_must_be_a_colour(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "tile.toml").write_text('[accents]\nFAKE = "orange"\n', "utf-8")
    with pytest.raises(PackError, match="accents must be colours"):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_an_advisor_pack_has_no_keys_to_press(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    (folder / "commands.toml").unlink()
    _edit(folder, "pack.toml", 'mode = "keys"', 'mode = "advisor"')
    _edit(folder, "pack.toml", 'window = "FakeGame.exe"\n', "")
    _edit(folder, "pack.toml", 'keybinds = "fake-game"\n', "")
    pack = load_pack(folder, built_in=False, registry=REGISTRY)
    assert (pack.mode, pack.commands, pack.window, pack.parts.keybinds) == (
        "advisor",
        None,
        None,
        None,
    )
    assert CommandMatcher.default(pack=pack).commands == ()


def test_only_packs_tycho_ships_load_by_name() -> None:
    with pytest.raises(PackError, match="no game pack 'fake-game'"):
        built_in("fake-game")
    with pytest.raises(PackError, match="no game pack"):
        built_in("../tests")
