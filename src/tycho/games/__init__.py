"""Every built-in part a game pack can name, listed once (ADR 18, ADR 19).

A pack names its parts by id in ``pack.toml``'s ``[parts]``; ``BUILT_IN`` says which
ids exist and, for game state and look-ups, how to build each one. No entry points and
no folder scans: a part is here, reviewed, or it doesn't exist.

Each builder imports its code when called, so importing this module stays cheap and
start-up stays as fast. Star Citizen's code is imported only here and in its own
folders (the layout test checks it).
"""

import logging
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tycho.packs.parts import (
    Built,
    GameDataError,
    GameFolder,
    GameKeys,
    HangarImport,
    Registry,
    Workshop,
)

__all__ = [
    "BUILT_IN",
    "HISTORY",
    "MEMORY",
    "SETTINGS",
    "GameDataError",
    "GameFolder",
    "GameKeys",
    "find_install",
    "fleet_findings",
    "fleet_ships",
    "game_findings",
    "game_root",
    "hangar_hint",
    "import_hangar",
    "load_keys",
    "ship_accent",
]

if TYPE_CHECKING:
    from tycho.commands import CommandMatcher
    from tycho.findings import Finding
    from tycho.games.star_citizen.owned import OwnedBlueprints
    from tycho.games.star_citizen.ship_lookup import ShipLookup
    from tycho.games.star_citizen.ships import ShipLibrary
    from tycho.packs import GamePack, Tile
    from tycho.packs.routes import Places
    from tycho.runtime.ports import GameState
    from tycho.settings import Settings

log = logging.getLogger("tycho")


# --- Star Citizen's install and keybinds, for the rest of Tycho ---------------------
# Defined here so nothing outside tycho.games imports Star Citizen's code (ADR 19).


def find_install(current: Settings) -> GameFolder:
    """Star Citizen's channel folder: the player's ``game_root`` first, then where the
    RSI Launcher says, then the usual places. ``FileNotFoundError`` if it isn't there."""
    from tycho.games.star_citizen import checks

    return checks.find_install(current)


def load_keys(install: GameFolder) -> GameKeys:
    """The player's keybinds in that install. ``GameDataError`` if they can't be read."""
    from tycho.games.star_citizen.install import GameInstall
    from tycho.games.star_citizen.store import KeybindStore

    return KeybindStore.load(cast(GameInstall, install))


def game_root(folder: Path, channel: str) -> Path | None:
    """The folder holding ``channel``, from whichever level the player picked."""
    from tycho.games.star_citizen.install import root_from

    return root_from(folder, channel)


def fleet_findings(pack: GamePack) -> Iterator[Finding]:
    """``tycho check``'s line on the player's fleet file, if it has a problem."""
    from tycho.games.star_citizen import checks

    yield from checks.fleet_findings(pack.id)


def fleet_ships(pack: GamePack) -> list[str] | None:
    """The ships the player told Tycho or imported, for the Memory page to show; None
    if the pack keeps no fleet."""
    if "fleet" not in pack.parts.lookups:
        return None
    from tycho import paths
    from tycho.games.star_citizen.fleet import Fleet

    return list(Fleet.load(paths.fleet_file(pack.id)).ships)


def hangar_hint(pack: GamePack) -> dict[str, str] | None:
    """What the Settings page says about importing a hangar (its ``label`` and
    ``hint``); None if the pack keeps no fleet to import into."""
    if "fleet" not in pack.parts.lookups:
        return None
    from tycho.games.star_citizen.hangar import HINT, LABEL

    return {"label": LABEL, "hint": HINT}


def import_hangar(pack: GamePack, file: Path) -> HangarImport:
    """A hangar export (``ships.csv`` or ``fleetview.json``) read and matched against
    the ship data, ready to save to the pack's fleet with the player's yes (#556).

    ``ValueError`` if the pack keeps no fleet or the file isn't an export; ``OSError``
    if the fleet file is broken; ``GameDataError`` if the ship data can't be had."""
    import asyncio

    from tycho import paths
    from tycho.games.star_citizen.data import ShipDataError, ensure
    from tycho.games.star_citizen.hangar import prepare
    from tycho.games.star_citizen.uex import UexApi
    from tycho.games.star_citizen.wiki import WikiApi

    if "fleet" not in pack.parts.lookups:
        raise ValueError(f"Tycho keeps no list of your ships in {pack.name}.")

    async def loaded() -> ShipLibrary:
        wiki, uex = WikiApi(), UexApi()
        try:
            return await ensure(wiki, paths.knowledge_dir(), concepts=uex)
        finally:  # no unclosed-connection noise after asyncio.run (#574)
            await wiki.aclose()
            await uex.aclose()

    try:
        library = asyncio.run(loaded())
    except ShipDataError as e:
        raise GameDataError(f"the game's data couldn't be loaded to match the names: {e}") from e
    return prepare(paths.fleet_file(pack.id), file, library)


def game_findings(
    current: Settings, matcher: CommandMatcher, *, ask: bool
) -> Generator[Finding, None, GameKeys | None]:
    """``tycho check``'s lines on Star Citizen: its folder (asked for, with ``ask``),
    keybinds and Game.log. Returns the keybinds, if they could be read."""
    from tycho.games.star_citizen import checks

    return (yield from checks.game_findings(current, matcher, ask=ask))


# --- Game state ----------------------------------------------------------------------


def _game_log(folder: Path) -> GameState:
    """Star Citizen's Game.log, read so far now, not on the first command."""
    from tycho.games.star_citizen.gamelog import GameLog

    game_log = GameLog(folder / "Game.log", _game_text(lambda: folder))
    game_log.facts()
    if not game_log.readable:
        log.info("No Game.log yet; Tycho reads it once the game writes one")
    return game_log


def _game_text(folder: Callable[[], Path | None]) -> Callable[[str], str]:
    """The game's text for a key (an item's name), read on first need from its loose
    global.ini or the keybind store's copy from Data.p4k (one build at a time)."""
    from tycho.games.star_citizen import localization
    from tycho.games.star_citizen.store import default_cache_dir

    def loose() -> Path | None:
        found = folder()
        return found / "Data" / "Localization" / "english" / "global.ini" if found else None

    def cached() -> Path | None:
        return max(default_cache_dir().glob("global-*.ini"), default=None, key=_modified)

    return localization.lazy_names(loose, cached, keep="item_name")


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# --- Look-ups, in no particular order: the pack's order is the tools' order ----------


def _ship_lookup(workshop: Workshop) -> ShipLookup:
    from tycho import paths
    from tycho.games.star_citizen.ship_lookup import ShipLookup
    from tycho.games.star_citizen.uex import UexApi
    from tycho.games.star_citizen.wiki import WikiApi

    return workshop.shared(
        "ship_lookup",
        lambda: ShipLookup(WikiApi(), paths.knowledge_dir(), concepts=UexApi()),
    )


def _ships(workshop: Workshop) -> Built:
    from tycho.games.star_citizen.fleet import patch_line

    ships = _ship_lookup(workshop)
    return Built((ships,), about=lambda: patch_line(ships.library))


def _fleet(workshop: Workshop) -> Built:
    from tycho import paths
    from tycho.games.star_citizen.fleet import Fleet, FleetUpdate

    ships = _ship_lookup(workshop)
    fleet = Fleet.load(paths.fleet_file(workshop.pack.id))
    tile = workshop.pack.tile
    return Built(
        (FleetUpdate(fleet, lambda: ships.library),),
        about=lambda: fleet.describe(ships.library),
        accent=lambda: ship_accent(fleet.flying, ships.library, tile),
    )


def _items(workshop: Workshop) -> Built:
    """Items and blueprints, with the blueprints the player owns (ADR 8, #598)."""
    import functools

    from tycho import paths
    from tycho.games.star_citizen.blueprints import BlueprintLookup
    from tycho.games.star_citizen.item_lookup import ItemLookup
    from tycho.games.star_citizen.owned import OwnedBlueprints
    from tycho.games.star_citizen.owned_tool import OwnedTool
    from tycho.games.star_citizen.wiki import WikiApi

    current = workshop.current

    @functools.cache
    def folder() -> Path | None:
        from tycho.games.star_citizen import checks

        try:
            return checks.find_install(current).folder
        except FileNotFoundError, OSError:
            return None

    owned = OwnedBlueprints(
        paths.blueprints_file(workshop.pack.id),
        folder,
        _game_text(folder),
        on=current.memory,
    )
    _also_live(workshop, "memory", owned.set_on)
    wiki = WikiApi()
    blueprints = BlueprintLookup(wiki, owned=owned)
    return Built((ItemLookup(wiki), blueprints, OwnedTool(owned, blueprints.blueprint_names)))


def owned_blueprints(pack: GamePack) -> OwnedBlueprints | None:
    """The player's blueprint list (#598) as its file has it, for the Memory page; None
    for a game without one. It reads no log: the brain's look-ups do."""
    from tycho import paths
    from tycho.games.star_citizen.owned import OwnedBlueprints

    if "items" not in pack.parts.lookups:
        return None
    return OwnedBlueprints(paths.blueprints_file(pack.id), lambda: None)


def _also_live(workshop: Workshop, name: str, apply: Callable[[object], None]) -> None:
    """Apply setting ``name`` when it changes by voice, beside what already does."""
    if workshop.live is None:
        return
    before = workshop.live.get(name)

    def both(value: object) -> None:
        if before is not None:
            before(value)
        apply(value)

    workshop.live[name] = both


def _mining(_: Workshop) -> Built:
    from tycho.games.star_citizen.mining import MiningLookup
    from tycho.games.star_citizen.wiki import WikiApi

    return Built((MiningLookup(WikiApi()),))


def _trade(_: Workshop) -> Built:
    from tycho.games.star_citizen.trade_lookup import trade_lookups
    from tycho.games.star_citizen.uex import UexApi

    return Built(tuple(trade_lookups(UexApi())))


def _places() -> Places:
    from tycho.games.star_citizen.places import UexPlaces
    from tycho.games.star_citizen.uex import UexApi

    uex = UexApi()
    return UexPlaces(uex, uex.aclose)  # closed with the session (#644)


def _settings(workshop: Workshop) -> Built:
    from tycho.spoken_settings import SettingsTool

    return Built((SettingsTool(live=workshop.live, offered=workshop.pack.callouts),))


def _memory(workshop: Workshop) -> Built:
    """The player's notes (ADR 8, #483): every game's and this game's, in each request's
    header while ``memory`` is on, and the tool that saves and forgets them."""
    from tycho import paths
    from tycho.memory import Memory, NoteFile
    from tycho.memory.tool import MemoryTool

    memory = Memory(
        NoteFile(paths.memory_file()),
        NoteFile(paths.game_memory_file(workshop.pack.id)),
        on=workshop.current.memory,
    )
    _also_live(workshop, "memory", memory.set_on)
    return Built((MemoryTool(memory),), about=memory.block)


def _history(workshop: Workshop) -> Built:
    """Searching past conversations (ADR 14's amendment, #484), while ``keep_history``
    is on. Kept by the session (``tycho.history``); this only reads."""
    from tycho.history import follow, kept
    from tycho.history.tool import HistoryLookup

    history = kept(workshop.current.keep_history is True)
    if workshop.live is not None:
        follow(workshop.live, history)
    return Built((HistoryLookup(history),))


def _web(workshop: Workshop) -> Built:
    """Web search, if the player left it on: through OpenRouter's web plugin when they
    have an OpenRouter key (#365), otherwise Anthropic's own search."""
    current = workshop.current
    if not current.web_search:
        log.info("Web search: off (web_search is false)")
        return Built()
    import anthropic

    from tycho.knowledge.web import WebSearch

    search_key = workshop.search_key()
    anthropic_search = (
        None
        if search_key is None
        else WebSearch(
            anthropic.AsyncAnthropic(api_key=search_key, max_retries=0),
            log_queries=current.keep_transcripts,
            sites=workshop.pack.web_sites,
            fallback=current.web_search_open,
        )
    )
    if (openrouter := workshop.openrouter_key()) is not None:
        from tycho.knowledge.web_plugin import ENGINE, PluginSearch

        log.info("Web search: %s, through your OpenRouter key", ENGINE)
        return Built(
            (
                PluginSearch(
                    openrouter,
                    sites=workshop.pack.web_sites,
                    game=workshop.pack.name,
                    open_web=current.web_search_open,
                    log_queries=current.keep_transcripts,
                    fallback=anthropic_search,  # if OpenRouter refuses the key (#551)
                ),
            )
        )
    if anthropic_search is None:  # a brain runs on one of the two keys, so never in play
        log.info("Web search: no key to search with")
        return Built()
    return Built((anthropic_search,))


def ship_accent(flying: str, library: ShipLibrary | None, tile: Tile) -> str | None:
    """The Tile's colour for the ship the player said they're flying, if Tycho knows it,
    from the game pack's ``tile``."""
    from tycho.games.star_citizen.ships import Ship
    from tycho.tile.design import accent_for, accent_for_maker

    if not flying or library is None:
        return None
    found = library.find(flying)
    if not isinstance(found, Ship):
        return None
    if found.key.split("_", 1)[0].upper() in tile.accents:
        return accent_for(found.key, tile)
    return accent_for_maker(found.maker, tile)


SETTINGS = "settings"
"""A look-up every pack gets, named or not: the player's spoken settings."""
MEMORY = "memory"
"""A look-up every pack gets, named or not: the player's notes (ADR 8)."""
HISTORY = "history"
"""A look-up every pack gets, named or not: past conversations (ADR 14, #484)."""

BUILT_IN = Registry(
    # game/install.py: the RSI Launcher's library folder, and the player's channel.
    install=frozenset({"star-citizen"}),
    # game/store.py: Data.p4k's defaults with the player's actionmaps.xml on top.
    keybinds=frozenset({"star-citizen"}),
    # game/gamelog.py: Game.log, for what the player is doing now.
    game_state={"star-citizen": _game_log},
    # knowledge/ and spoken_settings.py.
    lookups={
        "ships": _ships,
        "fleet": _fleet,
        "items": _items,
        "mining": _mining,
        "trade": _trade,
        SETTINGS: _settings,
        MEMORY: _memory,
        HISTORY: _history,
        "web": _web,
    },
    # games/star_citizen/places.py: UEX's list, until the game's own (#588).
    places={"star-citizen": _places},
)
