"""Knowledge (ADR 15): what the brain can look up before it answers.

Each source is a boundary (``WikiSource``) with a fake in tests, and each look-up
satisfies ``tycho.brain.lookups.Lookup``. Data from outside is untrusted: it only
ever shapes what Tycho says, never what it presses.
"""

from tycho.knowledge.ship_lookup import ShipLookup
from tycho.knowledge.ships import Ambiguous, NotFound, Ship, ShipLibrary
from tycho.knowledge.wiki import WikiApi, WikiError, WikiSource

__all__ = [
    "Ambiguous",
    "NotFound",
    "Ship",
    "ShipLibrary",
    "ShipLookup",
    "WikiApi",
    "WikiError",
    "WikiSource",
]
