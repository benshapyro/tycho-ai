"""Loading a game pack: a folder of data that Tycho validates, never code (ADR 18).

``load_pack`` turns the folder into one frozen ``GamePack``, or refuses it with a
plain message (``PackError``). What a pack may hold:

    pack.toml      the manifest: id, name, version, format, mode, the built-in parts it uses
    briefing.md    the game part of the brain's briefing, placing the core sections
    facts.md       what's true in the current patch
    terms.txt      game words for the speech corrector
    commands.toml  fast-path commands and sequences (only in a pack with key actions)
    tile.toml      the Tile's accents, by maker
    safety.toml    the safety gate's lists: what asks first, and what a mode switches off
    eval/cases.toml  the pack's own brain eval cases (tools/bench/tycho_eval.py --pack)

Packs load only from inside Tycho for now (``built_in``): a folder a player adds isn't
loaded until ADR 8's trust questions are answered. ``built_in`` on the pack records
where it came from, because some lists may only come from a pack Tycho ships.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Literal, cast

from tycho.packs.parts import BUILT_IN, Registry

FORMATS = frozenset({1})
"""The folder formats this Tycho reads."""

STAR_CITIZEN = "star-citizen"
"""The pack Tycho uses when the settings name none."""

CORE_SECTIONS = (
    *("tools", "no-key", "settings", "tile", "unclear", "squad", "safety"),
    *("date", "recent", "voice"),
)
"""The core parts of the briefing (``brain/core.md``), which every pack's briefing.md
places once each, by a line of its own: ``{{core:safety}}``."""

MARKER = re.compile(r"^\{\{core:([a-z-]+)\}\}$", re.MULTILINE)
KEYS = re.compile(r"\{\{keys:([^|{}\n]*)\|([^|{}\n]*)\}\}")
"""``{{keys:with keys|without}}`` in a briefing: the first text when Tycho presses keys,
the second in advisor mode (#325), so a line that tells the brain to press has its own
words when it can't. Keys on, the briefing reads as if the marker were the first text."""

type Mode = Literal["keys", "advisor"]

_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_COLOUR = re.compile(r"#[0-9a-f]{6}")
_SITE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
_FILES = frozenset(
    {"pack.toml", "briefing.md", "facts.md", "terms.txt", "commands.toml", "tile.toml"}
    | {"safety.toml", "eval"}
)
_TOP = frozenset(
    {"id", "name", "version", "format", "mode", "why", "window", "parts", "web", "briefing"}
)
_PARTS = frozenset({"install", "keybinds", "game_state", "lookups"})


class PackError(Exception):
    """A pack that can't be used, and why, in words for the player."""


@dataclass(frozen=True, slots=True)
class Parts:
    """The built-in code a pack uses, by id (``packs.parts``)."""

    install: str | None = None
    keybinds: str | None = None
    game_state: str | None = None
    lookups: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Tile:
    accents: Mapping[str, str] = field(default_factory=dict[str, str])
    """The Tile's accent by the maker prefix of a ship's game name (``DRAK``)."""
    makers: Mapping[str, str] = field(default_factory=dict[str, str])
    """A maker's name, by its first word, to its prefix (``drake`` to ``DRAK``)."""


LOOSENING = ("not_danger", "mode_guards", "state_guards", "accepted_risks")
"""The safety lists that make a check let something through. Only a pack that ships
inside Tycho, reviewed like code, may set them (ADR 18)."""
_SAFETY = frozenset({"confirm", "travel", "facts", *LOOSENING})


@dataclass(frozen=True, slots=True)
class Safety:
    """A game's lists for the safety gate (``safety.toml``). The rules that use them, and
    the danger-word backstop on top of them, stay in ``safety.py``."""

    confirm: frozenset[str] = frozenset()
    """Actions that always ask first."""
    travel: frozenset[str] = frozenset()
    """Actions that ask when the brain chose them from looser words (ADR 14)."""
    facts: Mapping[str, str] = field(default_factory=dict[str, str])
    """Facts the game state can report, with what the gate says when one is missing."""
    not_danger: tuple[str, ...] = ()
    """Words in harmless action names the backstop would catch (built-in packs only)."""
    mode_guards: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    """Setup actions, and the control contexts they switch off (built-in packs only)."""
    state_guards: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    """Facts, and the control contexts they rule out (built-in packs only)."""
    accepted_risks: frozenset[str] = frozenset()
    """Contexts a command may accept the risk of (built-in packs only)."""
    keys_off: str = ""
    """Set when Tycho may press nothing (advisor mode, #191), to the sentence that says
    why. The gate's first check refuses every press while it's set. It comes from the
    pack's mode or the player's ``key_actions`` setting (``as_run``), never from
    ``safety.toml``."""


@dataclass(frozen=True, slots=True)
class GamePack:
    id: str
    name: str
    version: str
    format: int
    mode: Mode
    why: str
    """Why the game is in its mode, in the pack's words: the app and ``tycho check`` say
    it, and in advisor mode the brain is told it. Every advisor pack has one."""
    window: str | None
    """The program whose window must be in front for Tycho to press (None: never presses)."""
    parts: Parts
    briefing: str
    """The game part of the briefing, with its ``{{core:...}}`` markers."""
    facts: str
    terms: tuple[str, ...]
    commands: str | None
    """The built-in commands file's text, or None for a pack without key actions."""
    tile: Tile
    safety: Safety
    web_sites: tuple[str, ...]
    """The only sites web search may read for this game."""
    unbriefed_actionmaps: frozenset[str]
    """Action maps the brain isn't offered: nobody asks a co-pilot for them."""
    continuous: re.Pattern[str] | None
    """Actions held for as long as they should last, which one press can't do."""
    built_in: bool
    """Whether the pack ships inside Tycho, reviewed like code (ADR 18)."""


def load_pack(folder: Traversable, *, built_in: bool, registry: Registry = BUILT_IN) -> GamePack:
    """The pack in ``folder``, checked; ``PackError`` says what's wrong with it."""
    names = {item.name for item in folder.iterdir()}
    if "pack.toml" not in names:
        raise PackError(f"{folder.name}: no pack.toml")
    if unknown := sorted(n for n in names - _FILES if not _ignored(n)):
        raise PackError(f"{folder.name}: files a pack can't hold: {', '.join(unknown)}")
    manifest = _toml(folder, "pack.toml")
    where = f"{folder.name}/pack.toml"
    if extra := sorted(manifest.keys() - _TOP):
        raise PackError(f"{where}: unknown settings: {', '.join(extra)}")
    pack_format = manifest.get("format")
    if pack_format not in FORMATS:
        raise PackError(
            f"{where}: format {pack_format!r} isn't one this Tycho reads "
            f"({', '.join(map(str, sorted(FORMATS)))}); a newer Tycho may read it"
        )
    pack_id = _text(manifest, "id", where)
    if not _ID.fullmatch(pack_id):
        raise PackError(f"{where}: id {pack_id!r} must be lower-case letters, digits and dashes")
    version = _text(manifest, "version", where)
    if not _VERSION.fullmatch(version):
        raise PackError(f"{where}: version {version!r} must be like 1.0.0")
    mode = manifest.get("mode")
    if mode not in ("keys", "advisor"):
        raise PackError(f'{where}: mode must be "keys" or "advisor", not {mode!r}')
    window = manifest.get("window")
    if window is not None and (not isinstance(window, str) or not window.strip()):
        raise PackError(f"{where}: window must be a program's name, like Game.exe")
    parts = _parts(manifest.get("parts", {}), where, registry)
    commands = _read(folder, "commands.toml") if "commands.toml" in names else None
    why = manifest.get("why", "")
    if not isinstance(why, str):
        raise PackError(f"{where}: why must be some text")
    why = " ".join(why.split())
    if mode == "advisor":
        # An advisor never presses (#191): nothing in it may suggest it could.
        if commands is not None or parts.keybinds is not None or window is not None:
            raise PackError(
                f"{where}: an advisor pack can't have commands.toml, a keybinds part or a window"
            )
        if not why:
            raise PackError(
                f"{where}: an advisor pack must say why it presses no keys, in why: "
                "the player is told"
            )
    elif parts.keybinds is None or window is None:
        raise PackError(f"{where}: a pack with key actions needs a keybinds part and a window")
    if commands is not None:
        try:
            tomllib.loads(commands)
        except tomllib.TOMLDecodeError as e:
            raise PackError(f"{folder.name}/commands.toml: {e}") from e
    briefing = _read(folder, "briefing.md") if "briefing.md" in names else ""
    _check_briefing(briefing, f"{folder.name}/briefing.md")
    filters = _table(manifest, "briefing", where)
    name = _text(manifest, "name", where)
    lists = _safety(
        _toml(folder, "safety.toml") if "safety.toml" in names else {},
        f"{folder.name}/safety.toml",
        built_in=built_in,
    )
    if mode == "advisor":
        lists = replace(lists, keys_off=_keys_off(name, why))
    return GamePack(
        id=pack_id,
        name=name,
        version=version,
        format=cast(int, pack_format),
        mode=mode,
        why=why,
        window=window,
        parts=parts,
        briefing=briefing,
        facts=_read(folder, "facts.md").strip() if "facts.md" in names else "",
        terms=_terms(_read(folder, "terms.txt")) if "terms.txt" in names else (),
        commands=commands,
        tile=_tile(_toml(folder, "tile.toml") if "tile.toml" in names else {}, folder.name),
        safety=lists,
        web_sites=_sites(_table(manifest, "web", where), where),
        unbriefed_actionmaps=frozenset(_strings(filters, "unbriefed_actionmaps", where)),
        continuous=_pattern(filters.get("continuous"), where),
        built_in=built_in,
    )


YOU_TURNED_THEM_OFF = "You turned key actions off in Tycho's settings."
"""Why a game that allows key actions is in advisor mode: the player chose it."""


def as_run(pack: GamePack, *, key_actions: bool = True) -> GamePack:
    """The pack as Tycho runs it with the player's ``key_actions`` setting (#191).

    Off makes a pack with key actions an advisor: the gate refuses every press, and the
    brain gets no tool to press with. It can only make Tycho do less: in an advisor
    pack, the setting can't turn key actions on. The pack's commands stay, so a command
    the player says is refused with why, rather than passed to the brain as chat."""
    if key_actions or pack.mode == "advisor":
        return pack
    return replace(
        pack,
        mode="advisor",
        why=YOU_TURNED_THEM_OFF,
        safety=replace(pack.safety, keys_off=_keys_off(pack.name, YOU_TURNED_THEM_OFF)),
    )


def _keys_off(name: str, why: str) -> str:
    return f"Tycho doesn't press keys in {name}. {why}"


@cache
def built_in(pack_id: str = STAR_CITIZEN) -> GamePack:
    """A pack that ships inside Tycho, by id."""
    folder = resources.files("tycho.packs").joinpath(pack_id)
    if not _ID.fullmatch(pack_id) or not folder.is_dir():
        raise PackError(f"There's no game pack {pack_id!r}. Installed: {', '.join(installed())}")
    pack = load_pack(folder, built_in=True)
    if pack.id != pack_id:
        raise PackError(f"{pack_id}/pack.toml: its id is {pack.id!r}, not its folder's name")
    return pack


def installed() -> tuple[str, ...]:
    """The ids of the packs that ship inside Tycho."""
    root = resources.files("tycho.packs")
    return tuple(
        sorted(i.name for i in root.iterdir() if i.is_dir() and i.joinpath("pack.toml").is_file())
    )


def _ignored(name: str) -> bool:
    return name.startswith((".", "_")) or name in ("README.md", "AGENTS.md")


def _read(folder: Traversable, name: str) -> str:
    try:
        return folder.joinpath(name).read_text("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise PackError(f"{folder.name}/{name}: {e}") from e


def _toml(folder: Traversable, name: str) -> dict[str, object]:
    try:
        return tomllib.loads(_read(folder, name))
    except tomllib.TOMLDecodeError as e:
        raise PackError(f"{folder.name}/{name}: {e}") from e


def _text(table: Mapping[str, object], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PackError(f"{where}: {key} must be some text")
    return value


def _table(table: Mapping[str, object], key: str, where: str) -> dict[str, object]:
    value = table.get(key, {})
    if not isinstance(value, dict):
        raise PackError(f"{where}: [{key}] must be a table")
    return cast(dict[str, object], value)


def _strings(table: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    items = cast(list[object], value) if isinstance(value, list) else None
    if items is None or not all(isinstance(item, str) and item for item in items):
        raise PackError(f"{where}: {key} must be a list of text")
    return tuple(cast(list[str], items))


def _parts(table: object, where: str, registry: Registry) -> Parts:
    if not isinstance(table, dict):
        raise PackError(f"{where}: [parts] must be a table")
    parts = cast(dict[str, object], table)
    if extra := sorted(parts.keys() - _PARTS):
        raise PackError(f"{where}: unknown parts: {', '.join(extra)}")
    named: dict[str, str | None] = {}
    for kind in ("install", "keybinds", "game_state"):
        value = parts.get(kind)
        if value is not None and not isinstance(value, str):
            raise PackError(f"{where}: {kind} must be a part's id")
        if value is not None and value not in registry.known(kind):
            raise PackError(f"{where}: Tycho has no built-in {kind} part {value!r}")
        named[kind] = value
    lookups = _strings(parts, "lookups", where)
    if unknown := [name for name in lookups if name not in registry.lookups]:
        raise PackError(f"{where}: Tycho has no built-in look-up {', '.join(map(repr, unknown))}")
    return Parts(named["install"], named["keybinds"], named["game_state"], lookups)


def _sites(table: Mapping[str, object], where: str) -> tuple[str, ...]:
    sites = _strings(table, "sites", where)
    if bad := [site for site in sites if not _SITE.fullmatch(site)]:
        raise PackError(f"{where}: web sites must be bare domains, not {', '.join(bad)}")
    return sites


def _pattern(value: object, where: str) -> re.Pattern[str] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PackError(f"{where}: continuous must be a pattern")
    try:
        return re.compile(value)
    except re.error as e:
        raise PackError(f"{where}: continuous isn't a pattern Tycho can read: {e}") from e


def _terms(text: str) -> tuple[str, ...]:
    lines = (line.strip() for line in text.splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def _tile(table: Mapping[str, object], name: str) -> Tile:
    where = f"{name}/tile.toml"
    accents = _table(table, "accents", where)
    makers = _table(table, "makers", where)
    # Each value's type first: a list as a maker's accent can't be looked up (#319).
    if bad := [k for k, v in accents.items() if not (isinstance(v, str) and _COLOUR.fullmatch(v))]:
        raise PackError(f"{where}: accents must be colours like #4fb3ff: {', '.join(bad)}")
    if bad := [k for k, v in makers.items() if not isinstance(v, str)]:
        raise PackError(f"{where}: each maker must name an accent: {', '.join(bad)}")
    if bad := [k for k, v in makers.items() if v not in accents]:
        raise PackError(f"{where}: makers with no accent: {', '.join(bad)}")
    return Tile(
        MappingProxyType(cast(dict[str, str], accents)),
        MappingProxyType(cast(dict[str, str], makers)),
    )


def _check_briefing(text: str, where: str) -> None:
    """Every core section once, and nothing else in braces that looks like one."""
    if "{{keys:" in KEYS.sub("", text):
        raise PackError(f"{where}: a keys marker is {{{{keys:with keys|without}}}}, on one line")
    if "{{core:" in MARKER.sub("", text):
        raise PackError(
            f"{where}: a core marker must be a line of its own, like {{{{core:safety}}}}"
        )
    placed = MARKER.findall(text)
    if unknown := sorted(set(placed) - set(CORE_SECTIONS)):
        raise PackError(f"{where}: no core section {', '.join(unknown)}")
    if missing := [name for name in CORE_SECTIONS if name not in placed]:
        raise PackError(
            f"{where}: the core sections must all be placed; missing {', '.join(missing)}"
        )
    if twice := sorted({name for name in placed if placed.count(name) > 1}):
        raise PackError(f"{where}: core sections placed more than once: {', '.join(twice)}")


def _safety(table: Mapping[str, object], where: str, *, built_in: bool) -> Safety:
    if extra := sorted(table.keys() - _SAFETY):
        raise PackError(f"{where}: unknown lists: {', '.join(extra)}")
    if not built_in and (loosening := [name for name in LOOSENING if table.get(name)]):
        # "Eject without asking" from a pack Tycho didn't ship: refused, never trusted.
        raise PackError(
            f"{where}: only a pack that ships inside Tycho may set {', '.join(loosening)}: "
            "they let the safety gate allow something it would otherwise stop"
        )
    facts = _table(table, "facts", where)
    if bad := [k for k, v in facts.items() if not isinstance(v, str) or not v.strip()]:
        raise PackError(f"{where}: each fact needs the sentence to say when it's missing: {bad}")
    state_guards = _guards(table, "state_guards", where)
    if unknown := sorted(state_guards.keys() - facts.keys()):
        raise PackError(f"{where}: state_guards for facts it doesn't list: {', '.join(unknown)}")
    return Safety(
        confirm=frozenset(_strings(table, "confirm", where)),
        travel=frozenset(_strings(table, "travel", where)),
        # Read-only: the built-in packs are cached and shared by the whole process.
        facts=MappingProxyType(cast(dict[str, str], facts)),
        not_danger=_strings(table, "not_danger", where),
        mode_guards=MappingProxyType(_guards(table, "mode_guards", where)),
        state_guards=MappingProxyType(state_guards),
        accepted_risks=frozenset(_strings(table, "accepted_risks", where)),
    )


def _guards(table: Mapping[str, object], key: str, where: str) -> dict[str, frozenset[str]]:
    guards = _table(table, key, where)
    return {name: frozenset(_strings(guards, name, f"{where} [{key}]")) for name in guards}
