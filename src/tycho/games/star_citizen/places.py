"""Where a route may go: places' names from UEX's terminal list (#588, Ben's choice of
24 Nov 2026), until Tycho can read the game's own list.

A name from here is only what Tycho types into the map's search: the map's own result
must show it before anything more is clicked (``runtime/route.py``), so a name UEX has
and the map doesn't ends there, with nothing plotted.

Only places the map plots to: cities, stations, outposts, points of interest, moons
and planets. A shop's terminal, an orbit or a whole system isn't one.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from tycho.games.star_citizen.trade import (
    FILLER,
    GUESS_AMONG,
    PLACE_ALIASES,
    Trade,
    place_matches,
    squash,
)
from tycho.games.star_citizen.wiki import WikiError, WikiSource
from tycho.packs.routes import PlaceError
from tycho.text import normalize, similarity

log = logging.getLogger(__name__)

ROUTE_FIELDS = (
    "city_name",
    "space_station_name",
    "outpost_name",
    "poi_name",
    "moon_name",
    "planet_name",
)
"""Where a place's name can match, in order: a city before the planet it's on."""
SYSTEM = "star_system_name"


class UexPlaces:
    """``packs.routes.Places`` over UEX's terminal list, fetched once when first asked."""

    def __init__(
        self, source: WikiSource, closing: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        self._trade = Trade(source)
        self._closing = closing
        """Closes ``source``'s connections, if it has any (#644)."""

    async def find(self, said: str, system: str | None = None) -> str | None:
        try:
            await self._trade.load()
        except WikiError as e:
            raise PlaceError(f"UEX's list of places: {e}") from e
        return best_place(self._trade.terminals.values(), said, system)

    def guessed(self, said: str, name: str) -> bool:
        """A guess unless every word said (but filler) is a word of the name, or it's
        one of the names' short forms ("a18"): "lorvile" and "dlevsky" are guesses,
        "wide forest" for ARC-L1 Wide Forest Station isn't."""
        spoken = " ".join(normalize(said))
        if spoken in PLACE_ALIASES or squash(said) == squash(name):
            return False
        words = {squash(word) for word in normalize(name)}
        return not all(squash(w) in words for w in normalize(said) if w not in FILLER)

    def name_of(self, route_id: str) -> str | None:
        """ "levski_all-001" is Levski: the id's first word is a place's whole name."""
        first = re.split(r"[^a-z0-9]+", route_id.lower())[0]
        if not first:
            return None
        for terminal in self._trade.terminals.values():
            for field in ROUTE_FIELDS:
                value = terminal.get(field)
                if value and squash(str(value)) == first:
                    return str(value)
        return None

    async def close(self) -> None:
        closing, self._closing = self._closing, None
        if closing is not None:
            await closing()


def best_place(
    terminals: Iterable[Mapping[str, Any]], said: str, system: str | None = None
) -> str | None:
    """The place ``said`` most likely names: the whole name first, everywhere, then part
    of a name or a misspelling; one in ``system`` first, of any kind ("Pyro Gateway
    (Stanton)" in Stanton, #682; a station there before a city elsewhere, #684), then the
    kinds' order, then the shortest when a few fit ("Lorville" before "Lorville Gates").
    None when nothing fits, or more than ``GUESS_AMONG`` do ("Lagrange")."""
    said = PLACE_ALIASES.get(" ".join(normalize(said)), said)
    whole = squash(said)
    key = squash(" ".join(w for w in normalize(said) if w not in FILLER)) or whole
    if not key:
        return None
    rows = list(terminals)
    here = squash(system or "")
    for exact in (True, False):
        first: str | None = None  # the best match outside the player's system
        for field in ROUTE_FIELDS:
            values = {str(row[field]) for row in rows if row.get(field)}
            there = {str(r[field]) for r in rows if r.get(field) and _in(r, here)}
            found = sorted(
                (v for v in values if _fits(key, whole, v, exact=exact)),
                key=lambda v: (bool(here) and v not in there, len(v), v),
            )
            if len(found) > GUESS_AMONG:
                if first is None:
                    log.debug("%r could be %d places", said, len(found))
                    return None
                continue  # too many here to guess among: the match before stands
            if found and (not here or found[0] in there):
                return found[0]  # in the player's system, whatever its kind (#684)
            first = first or (found[0] if found else None)
        if first is not None:
            return first
    return _sounds_like(rows, key)


SOUNDS = (
    (r"ph", "f"),
    (r"ck", "k"),
    (r"w", "v"),
    (r"f", "v"),
    (r"[bp]", "v"),
    (r"y", "i"),
    (r"e", "i"),
    (r"z", "s"),
    (r"ia", "i"),
)
"""Letters speech-to-text swaps for one another in a name it doesn't know ("Lewski",
"Lefsky" for Levski: #647; "Lipsky", "Lepski": Ben's PC try, 25 Nov 2026; "Ariel" for
Arial: #682)."""
SOUNDS_ALIKE = 0.8
"""How alike, letter for letter, a misheard name and the place's must sound."""


def _sounds_like(rows: list[Mapping[str, Any]], key: str) -> str | None:
    """The one place whose name sounds most like ``key`` ("dlevsky" is Levski), if
    it's alike enough and nothing else is as close. The map's own search still has to
    list it before anything is clicked."""
    heard = _sound(key)
    if len(key) < 5:  # as said: "ariel" sounds "aril", still five letters said (#682)
        return None
    scored: dict[str, float] = {}
    for row in rows:
        for field in ROUTE_FIELDS:
            if value := row.get(field):
                scored[str(value)] = similarity(heard, _sound(squash(str(value))))
    ranked = sorted(scored.items(), key=lambda pair: -pair[1])
    if not ranked or ranked[0][1] < SOUNDS_ALIKE:
        return None
    if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] - 0.05:
        log.debug("%r sounds like %s and %s", key, ranked[0][0], ranked[1][0])
        return None
    return ranked[0][0]


def _in(row: Mapping[str, Any], system: str) -> bool:
    """Whether the terminal ``row`` is in ``system`` (squashed: "stanton")."""
    return bool(system) and squash(str(row.get(SYSTEM) or "")) == system


def _sound(text: str) -> str:
    for letters, sound in SOUNDS:
        text = re.sub(letters, sound, text)
    return re.sub(r"(.)\1+", r"\1", text)


def _fits(key: str, whole: str, value: str, *, exact: bool) -> bool:
    """``key`` (without filler words) fits ``value``; or, exactly, all that was said
    does ("The Pit" keeps its "the": review of #643)."""
    return place_matches(key, value, exact=exact) or (exact and squash(value) == whole)
