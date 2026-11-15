"""What a game pack says in setup (#364): ``pack.toml``'s ``[setup]`` table, checked as
data, and each built-in pack's own."""

import re
import shutil
from pathlib import Path

import pytest

from tests.fakes.fake_pack import ADVISOR, FOLDER, REGISTRY, fake_pack
from tycho.commands import CommandMatcher, Match
from tycho.packs import PackError, Setup, built_in, installed, load_pack

SETUP = """
[setup]
intro = "Tycho raises   your shield when you ask."
lookups = ["Hatches, from the game's files"]
sends = ["A hatch question sends the hatch's name to fakegame.example."]
examples = ["shield up", "open the hatch"]
tips = ["Play in a window."]
"""


def _with(tmp_path: Path, setup: str, source: object = FOLDER) -> Path:
    folder = tmp_path / "pack"
    shutil.copytree(Path(str(source)), folder)
    manifest = folder / "pack.toml"
    # Top-level keys end at the first table, so [setup] goes last.
    manifest.write_text(manifest.read_text("utf-8") + setup, "utf-8")
    return folder


def test_a_pack_without_setup_text_says_nothing() -> None:
    assert fake_pack().setup == Setup()


def test_a_packs_setup_text_is_read_as_plain_lines(tmp_path: Path) -> None:
    pack = load_pack(_with(tmp_path, SETUP), built_in=False, registry=REGISTRY)
    assert pack.setup == Setup(
        intro="Tycho raises your shield when you ask.",  # one line, however it was spaced
        lookups=("Hatches, from the game's files",),
        sends=("A hatch question sends the hatch's name to fakegame.example.",),
        examples=("shield up", "open the hatch"),
        tips=("Play in a window.",),
    )


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("intro =", "welcome =", "unknown setup text: welcome"),
        ('examples = ["shield up", "open the hatch"]', 'examples = ["shield up"]', "two or three"),
        ('"shield up", ', '"a", "b", "c", ', "two or three examples, not 4"),
        ('tips = ["Play in a window."]', "tips = [1]", "setup's tips must be text"),
        ('tips = ["Play in a window."]', 'tips = "Play"', "must be a list of text"),
        ('tips = ["Play in a window."]', 'tips = [""]', "none empty"),
        ('"Play in a window."', '"' + "x" * 241 + '"', "over 240 characters"),
        ("[setup]", '[setup]\nfolder = "In the launcher."', "the pack has no install part"),
    ],
)
def test_setup_text_that_isnt_right_is_refused_with_why(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    assert old in SETUP
    folder = _with(tmp_path, SETUP.replace(old, new, 1))
    with pytest.raises(PackError, match=re.escape(message)):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_an_advisor_pack_has_setup_text_too(tmp_path: Path) -> None:
    folder = _with(tmp_path, SETUP, ADVISOR)
    assert load_pack(folder, built_in=False, registry=REGISTRY).setup.examples[0] == "shield up"


@pytest.mark.parametrize("pack_id", installed())
def test_every_pack_tycho_ships_introduces_itself(pack_id: str) -> None:
    pack = built_in(pack_id)
    assert pack.setup.intro
    assert pack.setup.lookups
    assert 2 <= len(pack.setup.examples) <= 3
    # A folder to find comes with where to find it; a game without one has no such help.
    assert bool(pack.setup.folder) == (pack.parts.install is not None)
    # A game with look-ups of its own says what they send; one with none sends nothing.
    own = set(pack.parts.lookups) - {"settings", "web"}
    assert bool(pack.setup.sends) == bool(own)


@pytest.mark.parametrize("pack_id", [p for p in installed() if built_in(p).mode == "keys"])
def test_the_first_thing_to_say_is_one_of_the_packs_own_commands(pack_id: str) -> None:
    # It works at once, with no key for the brain: a first try that can't fail on a key.
    pack = built_in(pack_id)
    assert isinstance(CommandMatcher.default(pack=pack).match(pack.setup.examples[0]), Match)


@pytest.mark.parametrize("pack_id", installed())
def test_a_packs_setup_text_names_no_other_game(pack_id: str) -> None:
    pack = built_in(pack_id)
    text = pack.setup
    lines = [text.intro, text.folder, *text.lookups, *text.sends, *text.examples, *text.tips]
    said = " ".join(lines)
    for other in installed():
        if other != pack_id:
            assert built_in(other).name not in said
