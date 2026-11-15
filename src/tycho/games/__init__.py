"""Every built-in part a game pack can name, listed once (ADR 18, ADR 19).

A pack names its parts by id in ``pack.toml``'s ``[parts]``; ``BUILT_IN`` says which
ids exist and, for game state and look-ups, how to build each one. No entry points and
no folder scans: a part is here, reviewed, or it doesn't exist.

Each builder imports its code when called, so importing this module stays cheap and
start-up stays as fast. Star Citizen's code is imported only here and in its own
folders (the layout test checks it).
"""

import logging
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tycho.packs.parts import Built, GameDataError, GameFolder, GameKeys, Registry, Workshop

__all__ = ["BUILT_IN", "GameDataError", "find_install", "game_findings", "load_keys"]

if TYPE_CHECKING:
    from tycho.commands import CommandMatcher
    from tycho.findings import Finding
    from tycho.games.star_citizen.ship_lookup import ShipLookup
    from tycho.games.star_citizen.ships import ShipLibrary
    from tycho.packs import GamePack, Tile
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


def ask_game_root(channel: str) -> Path | None:
    """Ask in the terminal where Star Citizen is; None if the player skips."""
    from tycho.games.star_citizen import checks

    return checks.ask_game_root(channel)


def fleet_findings(pack: GamePack) -> Iterator[Finding]:
    """``tycho check``'s line on the player's fleet file, if it has a problem."""
    from tycho.games.star_citizen import checks

    yield from checks.fleet_findings(pack.id)


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

    game_log = GameLog(folder / "Game.log")
    game_log.facts()
    if not game_log.readable:
        log.info("No Game.log yet; Tycho reads it once the game writes one")
    return game_log


# --- Look-ups, in no particular order: the pack's order is the tools' order ----------


def _ship_lookup(workshop: Workshop) -> ShipLookup:
    from tycho import paths
    from tycho.games.star_citizen import ShipLookup, UexApi, WikiApi

    return workshop.shared(
        "ship_lookup",
        lambda: ShipLookup(WikiApi(), paths.knowledge_dir(), concepts=UexApi()),
    )


def _ships(workshop: Workshop) -> Built:
    return Built((_ship_lookup(workshop),))


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


def _items(_: Workshop) -> Built:
    from tycho.games.star_citizen import WikiApi
    from tycho.games.star_citizen.item_lookup import ItemLookup

    return Built((ItemLookup(WikiApi()),))


def _mining(_: Workshop) -> Built:
    from tycho.games.star_citizen import WikiApi
    from tycho.games.star_citizen.mining import MiningLookup

    return Built((MiningLookup(WikiApi()),))


def _trade(_: Workshop) -> Built:
    from tycho.games.star_citizen import UexApi
    from tycho.games.star_citizen.trade_lookup import trade_lookups

    return Built(tuple(trade_lookups(UexApi())))


def _settings(workshop: Workshop) -> Built:
    from tycho.spoken_settings import SettingsTool

    return Built((SettingsTool(live=workshop.live),))


def _web(workshop: Workshop) -> Built:
    """Web search on the pack's sites, if the player left it on and has an Anthropic key."""
    current = workshop.current
    if not current.web_search:
        log.info("Web search: off (web_search is false)")
        return Built()
    import anthropic

    from tycho.findings import key_help
    from tycho.knowledge.web import SearchNeedsAnthropic, WebSearch

    search_key = workshop.search_key()
    if search_key is None:
        log.info("Web search: needs an Anthropic key (%s); Tycho says so", key_help("anthropic"))
        return Built((SearchNeedsAnthropic(),))
    if workshop.brain_provider != "anthropic":
        log.info("Web search: through your Anthropic key")
    return Built(
        (
            WebSearch(
                anthropic.AsyncAnthropic(api_key=search_key, max_retries=0),
                log_queries=current.keep_transcripts,
                sites=workshop.pack.web_sites,
                fallback=current.web_search_open,
            ),
        )
    )


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
"""The look-up every pack gets, named or not: the player's spoken settings."""

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
        "web": _web,
    },
)
