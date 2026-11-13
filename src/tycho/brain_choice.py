"""Which brain Tycho runs, and what ``tycho check`` says about it (#112, #61).

The player picks the provider and model in the settings (``brain_provider``,
``brain_model``), from the allowlist in ``brain.providers``. This module turns that
choice and the stored key into a running brain with its look-ups, and into the
check's lines. Split out of ``app.py``, which only wires the result in.
"""

import logging
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from tycho import paths
from tycho.brain.providers import Choice, choose
from tycho.findings import Finding, key_finding, key_help, stored_key
from tycho.packs import GamePack, built_in

if TYPE_CHECKING:
    from tycho.address import Address
    from tycho.brain.lookups import Lookup
    from tycho.brain.traces import TraceFile
    from tycho.commands import CommandMatcher
    from tycho.desktop.keys import KeyCheck
    from tycho.knowledge.ships import ShipLibrary
    from tycho.runtime import Brain
    from tycho.settings import Settings
    from tycho.spoken_settings import Live
    from tycho.tile.display import TileDisplay

log = logging.getLogger("tycho")

BRAIN_KEY_HELP = key_help("anthropic")
WITHOUT_BRAIN = "only built-in phrasings work"


def chosen(current: Settings) -> Choice:
    """The brain the settings pick (the schema already refused one not offered)."""
    return choose(current.brain_provider, current.brain_model)


def anthropic_key() -> str | None:
    return stored_key("anthropic", "Anthropic", BRAIN_KEY_HELP)


def openrouter_key() -> str | None:
    return stored_key("openrouter", "OpenRouter", key_help("openrouter"))


def brain_key(choice: Choice) -> str | None:
    """The key for the chosen provider, or None if there's none Tycho can use."""
    return anthropic_key() if choice.provider == "anthropic" else openrouter_key()


def load_brain(
    matcher: CommandMatcher,
    current: Settings,
    live: Live | None = None,
    address: Address | None = None,
    display: TileDisplay | None = None,
    pack: GamePack | None = None,
) -> Brain | None:
    """The brain the settings pick, if the player stored its provider's key. Without
    one, the fast path only. ``live`` applies settings changed by voice to the running
    session; ``address`` is what to call the player (#15), read fresh for each request.
    With a ``display``, the brain may show cards while the Tile is on, in the colour of
    the ship being flown. ``pack`` is the game (Star Citizen when None): its briefing,
    and only the look-ups it names (ADR 18)."""
    pack = pack or built_in()
    choice = chosen(current)
    key = brain_key(choice)
    if key is None:
        log.info("No %s key, so %s. Add one: %s", choice.label, WITHOUT_BRAIN, key_help(choice.key))
        return None
    import anthropic

    from tycho.knowledge import ShipLookup, UexApi, WikiApi
    from tycho.knowledge.fleet import Fleet, FleetUpdate
    from tycho.knowledge.item_lookup import ItemLookup
    from tycho.knowledge.mining import MiningLookup
    from tycho.knowledge.trade_lookup import trade_lookups
    from tycho.knowledge.web import SearchNeedsAnthropic, WebSearch
    from tycho.spoken_settings import SettingsTool

    log.info("Brain: %s, for anything the built-in phrasings don't cover", choice.describe())
    # Only the parts the pack names are built (#339): no ship data, fleet file or fleet
    # line for a game without them. The order is the tools' order: keep it (the cache).
    wanted = {*pack.parts.lookups, "settings"}
    ships = (
        ShipLookup(WikiApi(), paths.knowledge_dir(), concepts=UexApi())
        if wanted & {"ships", "fleet"}
        else None
    )
    fleet = Fleet.load(paths.fleet_file(pack.id)) if "fleet" in wanted else None
    lookups: list[Lookup] = []
    if ships is not None and "ships" in wanted:
        lookups.append(ships)
    if ships is not None and fleet is not None:
        lookups.append(FleetUpdate(fleet, lambda: ships.library))
    if "items" in wanted:
        lookups.append(ItemLookup(WikiApi()))
    if "mining" in wanted:
        lookups.append(MiningLookup(WikiApi()))
    if "trade" in wanted:
        lookups.extend(trade_lookups(UexApi()))
    lookups.append(SettingsTool(live=live))  # core: every pack gets it
    if "web" not in wanted:
        log.info("Web search: %s has none", pack.name)
    elif not current.web_search:
        log.info("Web search: off (web_search is false)")
    else:
        search_key = key if choice.provider == "anthropic" else anthropic_key()
        if search_key is None:
            log.info("Web search: needs an Anthropic key (%s); Tycho says so", BRAIN_KEY_HELP)
            lookups.append(SearchNeedsAnthropic())
        else:
            if choice.provider != "anthropic":
                log.info("Web search: through your Anthropic key")
            lookups.append(
                WebSearch(
                    anthropic.AsyncAnthropic(api_key=search_key, max_retries=0),
                    log_queries=current.keep_transcripts,
                    sites=pack.web_sites,
                    fallback=current.web_search_open,
                )
            )

    def about() -> str:
        flown = fleet.describe(ships.library) if fleet is not None and ships is not None else ""
        parts = [flown, address.header() if address else ""]
        return "\n".join(p for p in parts if p)

    if display is not None and fleet is not None and ships is not None:
        display.ship_accent = lambda: ship_accent(fleet.flying, ships.library)
    return make_brain(
        choice,
        key,
        matcher,
        lookups=lookups,
        about=about,
        cards=lambda: display is not None and display.on,
        pack=pack,
    )


def make_brain(
    choice: Choice,
    key: str,
    matcher: CommandMatcher,
    *,
    lookups: Sequence[Lookup] = (),
    about: Callable[[], str] = lambda: "",
    cards: Callable[[], bool] = lambda: False,
    pack: GamePack | None = None,
) -> Brain:
    """The adapter for the chosen provider, on the chosen model, briefed for ``pack``."""
    if choice.provider == "openrouter":
        from tycho.brain.openrouter import OpenRouterBrain

        return OpenRouterBrain(
            matcher,
            model=choice.model_id,
            api_key=key,
            lookups=lookups,
            about=about,
            cards=cards,
            traces=_traces(),
            pack=pack,
        )
    from tycho.brain.claude import ClaudeBrain

    return ClaudeBrain(
        matcher,
        api_key=key,
        model=choice.model_id,
        lookups=lookups,
        about=about,
        cards=cards,
        traces=_traces(),
        pack=pack,
    )


def _traces() -> TraceFile | None:
    """The brain's trace file when ``TYCHO_TRACE`` is on (#282): for Claude sessions
    reading what the brain did, never on for players by default."""
    from tycho.brain.traces import TraceFile

    traces = TraceFile.from_env()
    if traces is not None:
        log.info("Tracing the brain's requests to %s", traces.path)
    return traces


def ship_accent(flying: str, library: ShipLibrary | None) -> str | None:
    """The Tile's colour for the ship the player said they're flying, if Tycho knows it."""
    from tycho.knowledge.ships import Ship
    from tycho.tile.design import MAKER_ACCENTS, accent_for, accent_for_maker

    if not flying or library is None:
        return None
    found = library.find(flying)
    if not isinstance(found, Ship):
        return None
    if found.key.split("_", 1)[0].upper() in MAKER_ACCENTS:
        return accent_for(found.key)
    return accent_for_maker(found.maker)


def brain_findings(
    current: Settings, *, quick: bool = False, check: KeyCheck | None = None
) -> Iterator[Finding]:
    """The chosen provider and whether its key works: ``tycho check`` asks the provider
    (free, ``check``); the quick checks only look that it's stored. Then, through
    OpenRouter, whether web search has the Anthropic key it needs."""
    choice = chosen(current)
    fix = key_help(choice.key)
    key = brain_key(choice)
    if key is None:
        yield key_finding(choice.key, "Brain", choice.label, WITHOUT_BRAIN, fix)
    elif quick:
        yield Finding("ok", "Brain", f"{choice.describe()}, with your {choice.label} key")
    else:
        if check is None:
            from tycho.desktop.keys import ProviderKeyCheck

            check = ProviderKeyCheck()
        if problem := check.check(choice.key, key):
            yield Finding("warn", "Brain", f"{choice.describe()}: {problem}", fix=fix)
        else:
            yield Finding("ok", "Brain", f"{choice.describe()}; your {choice.label} key works")
    if choice.provider != "anthropic" and current.web_search and anthropic_key() is None:
        yield Finding(
            "warn",
            "Web search",
            f"needs an Anthropic key, even with the brain through {choice.label}; without "
            f"one Tycho says it can't search: {BRAIN_KEY_HELP}, or set web_search = false",
            fix=BRAIN_KEY_HELP,
        )
