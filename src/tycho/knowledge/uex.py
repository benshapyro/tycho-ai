"""UEX (uexcorp.space): community data on Star Citizen, including ships still in concept.

Public, no token needed for reads. Its terms allow personal, non-commercial use
and ask for no attribution; the README credits it anyway. Like the Wiki API's
data, nothing from it is kept in the repository (ADR 15).

The game files only hold ships that are in the game. UEX also lists the ones
announced and sold as concepts (the Galaxy, the Kraken), so Tycho can say what
they are and that they aren't flyable yet.
"""

import logging
from typing import Any, cast

import httpx2

from tycho.knowledge.wiki import WikiApi, WikiError, WikiSource

log = logging.getLogger(__name__)

UEX_URL = "https://api.uexcorp.uk/2.0"


class UexApi(WikiApi):
    """The real UEX API: the same client as the Wiki API's, at UEX's address."""

    def __init__(self, client: httpx2.AsyncClient | None = None) -> None:
        super().__init__(client, base_url=UEX_URL)


async def concept_vehicles(source: WikiSource) -> list[dict[str, Any]]:
    """Every ship and vehicle UEX marks as a concept, without add-on modules (one request)."""
    data = await source.get("/vehicles")
    try:
        records = cast(list[dict[str, Any]], data["data"])
        concepts = [r for r in records if r.get("is_concept") and not r.get("is_addon")]
    except (KeyError, TypeError, AttributeError) as e:
        raise WikiError("unexpected UEX vehicles list") from e
    log.debug("UEX lists %d concept ships and vehicles", len(concepts))
    return concepts
