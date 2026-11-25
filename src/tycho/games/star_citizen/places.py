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
from collections.abc import Iterable, Mapping
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
from tycho.text import normalize

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


class UexPlaces:
    """``packs.routes.Places`` over UEX's terminal list, fetched once when first asked."""

    def __init__(self, source: WikiSource) -> None:
        self._trade = Trade(source)

    async def find(self, said: str) -> str | None:
        try:
            await self._trade.load()
        except WikiError as e:
            raise PlaceError(f"UEX's list of places: {e}") from e
        return best_place(self._trade.terminals.values(), said)

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


def best_place(terminals: Iterable[Mapping[str, Any]], said: str) -> str | None:
    """The place ``said`` most likely names: the whole name first, everywhere, then part
    of a name or a misspelling; the shortest when a few fit ("Lorville" before "Lorville
    Gates"). None when nothing fits, or more than ``GUESS_AMONG`` do ("Lagrange")."""
    said = PLACE_ALIASES.get(" ".join(normalize(said)), said)
    whole = squash(said)
    key = squash(" ".join(w for w in normalize(said) if w not in FILLER)) or whole
    if not key:
        return None
    rows = list(terminals)
    for exact in (True, False):
        for field in ROUTE_FIELDS:
            values = {str(row[field]) for row in rows if row.get(field)}
            found = sorted(
                (v for v in values if _fits(key, whole, v, exact=exact)), key=lambda v: (len(v), v)
            )
            if len(found) > GUESS_AMONG:
                log.debug("%r could be %d places", said, len(found))
                return None
            if found:
                return found[0]
    return None


def _fits(key: str, whole: str, value: str, *, exact: bool) -> bool:
    """``key`` (without filler words) fits ``value``; or, exactly, all that was said
    does ("The Pit" keeps its "the": review of #643)."""
    return place_matches(key, value, exact=exact) or (exact and squash(value) == whole)
