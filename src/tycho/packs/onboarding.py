"""What a game pack says in setup (#364): ``pack.toml``'s ``[setup]`` table.

Setup holds no game words of its own. The pack supplies them, as data Tycho checks and
never runs (ADR 18): what Tycho does in that game, what it can look up, what those
look-ups send, two or three things to say first, and where to find the game's folder.
The page shows them as text, escaped; nothing here is a link or markup.

    [setup]
    intro = "Tycho presses your own keybinds when you ask, and answers questions."
    lookups = ["Ships, from the current patch's game files"]
    sends = ["Trade questions send the commodity or place you named to UEX Corp."]
    examples = ["gear down", "lights on", "how much cargo does the Caterpillar hold?"]
    tips = ["Play in Borderless Window mode so the Tile can show over the game."]
    folder = "The launcher shows it under Settings, Games, Library Folder."
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

LINE = 240
"""The longest line of setup text: it's read on one card, not a page."""
MOST = 8
EXAMPLES = (2, 3)
"""How many things to say first a pack gives: enough to choose from, few enough to read."""
_KEYS = frozenset({"intro", "lookups", "sends", "examples", "tips", "folder"})


@dataclass(frozen=True, slots=True)
class Setup:
    intro: str = ""
    """What Tycho does in this game, in a sentence or two."""
    lookups: tuple[str, ...] = ()
    """What Tycho can look up here, each with where it comes from."""
    sends: tuple[str, ...] = ()
    """What those look-ups send off the PC, and to whom: setup's privacy step lists them."""
    examples: tuple[str, ...] = ()
    """Two or three things to say first. In a pack with key actions the first is one of
    its own commands (a test checks), so the first try works without any key."""
    tips: tuple[str, ...] = ()
    """What to set in the game for Tycho to work well there."""
    folder: str = ""
    """Where the player finds the game's folder. Only a pack with an install part has one."""


def read(table: object, where: str, *, has_folder: bool) -> Setup:
    """``[setup]`` checked; ``ValueError`` says what's wrong with it. ``has_folder``:
    whether the pack has an install part, so a folder to find."""
    if not isinstance(table, dict):
        raise ValueError(f"{where}: [setup] must be a table")
    setup = cast(Mapping[str, object], table)
    if extra := sorted(setup.keys() - _KEYS):
        raise ValueError(f"{where}: unknown setup text: {', '.join(extra)}")
    examples = _lines(setup, "examples", where)
    if examples and not EXAMPLES[0] <= len(examples) <= EXAMPLES[1]:
        raise ValueError(f"{where}: setup needs two or three examples, not {len(examples)}")
    folder = _line(setup.get("folder", ""), "folder", where)
    if folder and not has_folder:
        raise ValueError(f"{where}: setup has folder help, but the pack has no install part")
    return Setup(
        intro=_line(setup.get("intro", ""), "intro", where),
        lookups=_lines(setup, "lookups", where),
        sends=_lines(setup, "sends", where),
        examples=examples,
        tips=_lines(setup, "tips", where),
        folder=folder,
    )


def _line(value: object, key: str, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where}: setup's {key} must be text")
    text = " ".join(value.split())
    if len(text) > LINE:
        raise ValueError(f"{where}: setup's {key} is over {LINE} characters")
    return text


def _lines(table: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{where}: setup's {key} must be a list of text")
    items = tuple(_line(item, key, where) for item in cast(list[object], value))
    if len(items) > MOST or not all(items):
        raise ValueError(f"{where}: setup's {key} must be up to {MOST} lines, none empty")
    return items
