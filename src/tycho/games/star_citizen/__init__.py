"""Star Citizen's code (ADR 19): its install, ``Data.p4k`` and keybind readers, the
keybind store, ``Game.log``, and its look-ups (the Wiki, UEX, ships, the fleet, items,
mining, refining and trade). Its data is the pack in ``packs/star-citizen/``.

Only ``tycho.games`` imports this package (the layout test checks it). Each source is
a boundary (``WikiSource``) with a fake in tests, and each look-up satisfies
``tycho.brain.lookups.Lookup``. Data from outside is untrusted: it only ever shapes
what Tycho says, never what it presses.
"""

from tycho.games.star_citizen.ship_lookup import ShipLookup
from tycho.games.star_citizen.ships import Ambiguous, NotFound, Ship, ShipLibrary
from tycho.games.star_citizen.trade_lookup import CommodityPrices, TradeRoutes, trade_lookups
from tycho.games.star_citizen.uex import UexApi
from tycho.games.star_citizen.wiki import WikiApi, WikiError, WikiSource

__all__ = [
    "Ambiguous",
    "CommodityPrices",
    "NotFound",
    "Ship",
    "ShipLibrary",
    "ShipLookup",
    "TradeRoutes",
    "UexApi",
    "WikiApi",
    "WikiError",
    "WikiSource",
    "trade_lookups",
]
