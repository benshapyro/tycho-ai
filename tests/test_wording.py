"""The wording rules a test can check (docs/voice-and-tone.md, #363): a game's own words
come from its pack, never from the core, so one game's words can't reach another game's
player. Read from the source, like ``test_code_layout``."""

import ast
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tycho import doctor, settings
from tycho.findings import Finding
from tycho.packs import STAR_CITIZEN, as_run, built_in, installed

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tycho"

GAME_WORDS = re.compile(
    r"Star Citizen|\bquantum\b|\bRSI\b|\bUEX\b|\bSCU\b|\baUEC\b|Laranite|mobiGlas|"
    r"\bCommander\b|\bcockpit\b|\bships?\b|\bflying\b|\bcrewmate\b|"
    r"World of Warcraft|\bWoW\b|Azeroth|Blizzard",
    re.IGNORECASE,
)
"""Words that belong to one game. A new pack adds its own."""

GAME_CODE = ("games/star_citizen/", "packs/star-citizen/", "packs/wow-forever/")
"""Where a game's words belong: its own code and its own pack."""

KNOWN: dict[str, set[str]] = {
    # The product's own description, the credits, and Star Citizen's own settings.
    "cli/__init__.py": {"star citizen"},
    "cli/commands.py": {"star citizen", "uex"},
    "settings.py": {"commander", "rsi", "ship", "star citizen", "wow"},
    # Not a game's words: a voice's name (The Ship's Mind), a look-up's id, and "a
    # pack that ships inside Tycho".
    "voice/voices.py": {"ship", "ships"},
    "games/__init__.py": {"ships"},
    "packs/pack.py": {"ships"},
    # The Tile preview's caption. Setup's own game words went to the packs (#364).
    "desktop/app/app.js": {"cockpit"},
    "desktop/setup.py": {"star citizen"},
    # What the brain is sent in every game. Rewording these changes Star Citizen's
    # prompt, so it needs Star Citizen's eval, on the maintainer's PC (#421).
    "address.py": {"crewmate"},
    "brain/core.md": {"commander", "flying", "quantum"},
    "tile/cards.py": {"laranite", "scu", "ship", "ships", "uex"},
    "knowledge/web.py": {"flying", "rsi", "ships", "star citizen", "uex"},
}
"""Today's breaks, by file: this list only shrinks. When you fix one, take it off."""


def _texts(path: Path) -> Iterator[str]:
    """What a player or the brain can be shown from a file: a Python file's strings
    (not its docstrings, which only a developer reads), or all of a page or briefing."""
    if path.suffix != ".py":
        yield re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.DOTALL)
        return
    tree = ast.parse(path.read_text("utf-8"))
    notes = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in notes:
            yield node.value


def _game_words() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    files = [
        *SRC.rglob("*.py"),
        *(SRC / "desktop" / "app").iterdir(),
        *(SRC / "brain").glob("*.md"),
    ]
    for path in sorted(files):
        name = path.relative_to(SRC).as_posix()
        if name.startswith(GAME_CODE) or path.suffix == ".css" or path.name == "AGENTS.md":
            continue
        words = {m.group(0).lower() for text in _texts(path) for m in GAME_WORDS.finditer(text)}
        if words:
            found[name] = words
    return found


def test_the_core_s_words_name_no_game() -> None:
    """A line in the core is said or shown in every game: "Switch to Star Citizen" was
    printed for a World of Warcraft player. A game's words come from its pack."""
    found = _game_words()
    new = {
        name: sorted(words - KNOWN.get(name, set()))
        for name, words in found.items()
        if words - KNOWN.get(name, set())
    }
    assert not new, f"a game's words in the core; take them from the pack: {new}"
    fixed = {
        name: sorted(words - found.get(name, set()))
        for name, words in KNOWN.items()
        if words - found.get(name, set())
    }
    assert not fixed, f"fixed: take these off KNOWN: {fixed}"


def test_a_setting_s_description_names_a_game_only_when_it_s_that_game_s_setting() -> None:
    """Every description is shown in the window's Settings and written to settings.toml,
    whichever game is chosen."""
    theirs = {"game", "game_root", "game_channel"}
    for name, field in settings.Settings.model_fields.items():
        said = GAME_WORDS.search(field.description or "")
        assert name in theirs or not said, f"{name}: {said and said.group(0)!r}"


def test_the_focus_check_names_the_chosen_game(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tycho.input import windows

    monkeypatch.setattr(windows, "foreground_executable", lambda: "")
    finding = doctor.focus_finding("Second Game", "Second.exe", seconds=0)
    assert "Switch to Second Game" in capsys.readouterr().out
    assert finding.detail.startswith("Second Game wasn't in front (another program was)")
    assert "Star Citizen" not in finding.detail


def test_a_game_tycho_presses_nothing_in_has_no_focus_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def focus(game: str, window: str) -> Finding:
        asked.append(game)
        return Finding("ok", "Focus check", game)

    monkeypatch.setattr(doctor, "focus_finding", focus)
    current = settings.Settings(check_for_updates=False)
    assert list(doctor.live_checks(current, built_in("wow-forever"))) == []
    keys_off = as_run(built_in(STAR_CITIZEN), key_actions=False)
    assert list(doctor.live_checks(current, keys_off)) == []
    assert [f.detail for f in doctor.live_checks(current, built_in(STAR_CITIZEN))] == [
        "Star Citizen"
    ]
    assert asked == ["Star Citizen"]


def _cases(pack_id: str) -> dict[str, Any]:
    return tomllib.loads((SRC / "packs" / pack_id / "eval" / "cases.toml").read_text("utf-8"))


def test_every_pack_s_eval_fails_a_reply_that_names_another_pack_s_game() -> None:
    """#363's bar: the eval fails on another game's words in a pack's replies. Each
    pack's cases list them (``never_say``), and the list covers every other pack."""
    names = {pack_id: built_in(pack_id).name for pack_id in installed()}
    for pack_id in names:
        never = [re.compile(str(p), re.IGNORECASE) for p in _cases(pack_id).get("never_say", [])]
        assert never, f"{pack_id}: its eval cases need never_say"
        for other, name in names.items():
            if other != pack_id:
                assert any(p.search(name) for p in never), f"{pack_id} lets {name!r} through"
