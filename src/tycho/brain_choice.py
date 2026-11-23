"""Which brain Tycho runs, and what ``tycho check`` says about it (#112, #61).

The player picks the provider and model in the settings (``brain_provider``,
``brain_model``), from the allowlist in ``brain.providers``. This module turns that
choice and the stored key into a running brain with its look-ups, and into the
check's lines. Split out of ``app.py``, which only wires the result in.
"""

import logging
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING

from tycho.brain.providers import Choice, choose
from tycho.findings import Finding, key_finding, key_help, stored_key
from tycho.packs import GamePack

if TYPE_CHECKING:
    from tycho.address import Address
    from tycho.brain.lookups import Lookup
    from tycho.brain.traces import TraceFile
    from tycho.commands import CommandMatcher
    from tycho.desktop.keys import KeyCheck
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
    *,
    pack: GamePack,
) -> Brain | None:
    """The brain the settings pick, if the player stored its provider's key. Without
    one, the fast path only. ``live`` applies settings changed by voice to the running
    session; ``address`` is what to call the player (#15), read fresh for each request.
    With a ``display``, the brain may show cards while the Tile is on, in the colour of
    the ship being flown. ``pack`` is the game: its briefing,
    and only the look-ups it names (ADR 18)."""
    choice = chosen(current)
    key = brain_key(choice)
    if key is None:
        log.info("No %s key, so %s. Add one: %s", choice.label, WITHOUT_BRAIN, key_help(choice.key))
        return None
    from tycho.games import BUILT_IN
    from tycho.packs import Workshop

    log.info("Brain: %s, for anything the built-in phrasings don't cover", choice.describe())
    # Only the parts the pack names are built (#339), by the registry, in the pack's
    # order: that is the tools' order, so keep it (the prompt cache).
    workshop = Workshop(
        pack,
        current,
        live,
        brain_provider=choice.provider,
        search_key=lambda: key if choice.provider == "anthropic" else anthropic_key(),
        openrouter_key=lambda: key if choice.provider == "openrouter" else openrouter_key(),
    )
    built = [BUILT_IN.lookups[name](workshop) for name in lookup_order(pack.parts.lookups)]
    if "web" not in pack.parts.lookups:
        log.info("Web search: %s has none", pack.name)
    lookups: list[Lookup] = [lookup for part in built for lookup in part.lookups]
    abouts = [part.about for part in built if part.about is not None]
    accents = [part.accent for part in built if part.accent is not None]

    def about() -> str:
        lines = [*(line() for line in abouts), address.header() if address else ""]
        return "\n".join(line for line in lines if line)

    if display is not None and accents:
        display.ship_accent = accents[0]
    return make_brain(
        choice,
        key,
        matcher,
        lookups=lookups,
        about=about,
        cards=lambda: display is not None and display.on,
        pack=pack,
    )


PLAYER_HOSTS = ("anthropic",)
"""Who serves a player's OpenRouter brain: Anthropic only, never Bedrock or Vertex, so
"What leaves your PC" in the README holds (#313). No fallback: if OpenRouter's Anthropic
route is down, a request fails, as Claude direct would."""


def lookup_order(named: Sequence[str]) -> list[str]:
    """The look-ups to build, in the pack's order, each once. ``settings``, ``memory`` and
    ``history`` are core: a pack that doesn't name them gets them before ``web``, or last."""
    from tycho.games import HISTORY, MEMORY, SETTINGS

    order = list(dict.fromkeys(named))
    for core in (SETTINGS, MEMORY, HISTORY):
        if core not in order:
            order.insert(order.index("web") if "web" in order else len(order), core)
    return order


def make_brain(
    choice: Choice,
    key: str,
    matcher: CommandMatcher,
    *,
    lookups: Sequence[Lookup] = (),
    about: Callable[[], str] = lambda: "",
    cards: Callable[[], bool] = lambda: False,
    pack: GamePack,
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
            hosts=PLAYER_HOSTS,
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
        elif spent := check.credit(choice.key, key):
            yield Finding("warn", "Brain", f"{choice.describe()}: the key works, but {spent}")
        else:
            yield Finding("ok", "Brain", f"{choice.describe()}; your {choice.label} key works")
