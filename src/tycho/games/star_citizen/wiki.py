"""The Star Citizen Wiki API (api.star-citizen.wiki): game data for the current patch.

Public, no key needed for reads. Its terms ask for credit in public projects and
forbid commercial use, and its data has no stated licence, so Tycho never ships a
copy: each player's PC downloads its own (ADR 15). Credit is in the README.

Paths are the API's current, unversioned ones (``/api/vehicles``, ``/api/items``):
``/api/v2/*`` still answers the same, but isn't in its OpenAPI spec (#153).
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx2

log = logging.getLogger(__name__)

BASE_URL = "https://api.star-citizen.wiki"
USER_AGENT = "Tycho (+https://github.com/benshapyro/tycho-ai)"
SEARCH_PATH = "/api/search"
VEHICLES = "vehicles"
ITEM_KINDS = frozenset({"items", "blueprints"})
"""The search's groups that name items: a blueprint is named after the item it makes."""
_UNSEARCHABLE = re.compile(r"[^\w\s.-]+")
"""The search refuses anything but letters, numbers, spaces and ``._-`` (HTTP 422)."""
SEARCH_LENGTH = 64


class WikiError(Exception):
    """A data API (the Wiki's or UEX's) couldn't be reached or answered with
    something unexpected."""


class WikiSource(Protocol):
    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        """The decoded JSON at ``path``. Raises ``WikiError`` on any failure."""
        ...


class WikiApi:
    """The real API, over HTTPS, with a clear User-Agent and a timeout on every call."""

    def __init__(
        self, client: httpx2.AsyncClient | None = None, *, base_url: str = BASE_URL
    ) -> None:
        self._client = client or httpx2.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx2.Timeout(30.0, connect=5.0),
        )

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        try:
            response = await self._client.get(path, params=dict(params or {}))
            response.raise_for_status()
            return response.json()
        except httpx2.HTTPStatusError as e:
            # Not str(e): it holds the whole URL, and a search's URL holds the words the
            # player said, which would reach the log file (audit, 2026-11-03).
            raise WikiError(f"{path}: HTTP {e.response.status_code}") from e
        except (httpx2.HTTPError, ValueError) as e:
            raise WikiError(f"{path}: {e}") from e

    async def aclose(self) -> None:
        """Close the connection pool. Tycho keeps one for the whole session; the bench
        tools make new ones each round (#213)."""
        await self._client.aclose()


@dataclass(frozen=True, slots=True)
class Hit:
    """One name the search found."""

    kind: str
    """Its group: "vehicles", "items", "blueprints", "commodities", "locations", "missions"."""
    name: str
    label: str | None = None
    """What kind of item ("FPS Weapon", "Paints"), or a vehicle's career ("Combat")."""


async def search(source: WikiSource, query: str) -> list[Hit]:
    """The API's unified search, ``GET /api/search?filter[query]=...``: up to five
    names in each group whose name or class name holds ``query`` (case aside, no
    near misses: "galant" finds nothing). About 0.3 s. Raises ``WikiError`` on any
    surprise, and never says what was searched for: those are the player's words."""
    cleaned = " ".join(_UNSEARCHABLE.sub(" ", query).split())[:SEARCH_LENGTH].strip()
    if len(cleaned) < 2:  # the API's least
        return []
    data = await source.get(SEARCH_PATH, {"filter[query]": cleaned})
    try:
        groups = cast(list[Any], data["data"])
        return [_hit(group, result) for group in groups for result in group["results"]]
    except (KeyError, TypeError, AttributeError) as e:
        raise WikiError(f"{SEARCH_PATH}: unexpected answer") from e


def _hit(group: Any, result: Any) -> Hit:
    kind, name = group["type"], result["name"]
    label = result.get("extra_label" if kind == VEHICLES else "item_type_label")
    if not (isinstance(kind, str) and isinstance(name, str) and name.strip()):
        raise TypeError("a search result without a type or name")
    return Hit(kind, name.strip(), label if isinstance(label, str) else None)


async def current_version(source: WikiSource) -> str:
    """The patch the Wiki API serves by default, e.g. "4.10.2-LIVE.12881860"."""
    data = await source.get("/api/game-versions")
    try:
        return next(str(v["code"]) for v in data["data"] if v.get("is_default"))
    except (KeyError, TypeError, StopIteration) as e:
        raise WikiError("no default game version") from e


async def all_vehicles(source: WikiSource) -> list[dict[str, Any]]:
    """Every ship and ground vehicle, a page at a time (about 11 requests)."""
    vehicles: list[dict[str, Any]] = []
    page, last = 1, 1
    while page <= last:
        data = await source.get("/api/vehicles", {"page": page})
        try:
            vehicles += data["data"]
            last = int(data["meta"]["last_page"])
        except (KeyError, TypeError, ValueError) as e:
            raise WikiError(f"unexpected vehicles page {page}") from e
        page += 1
    log.debug("Downloaded %d vehicles from the Star Citizen Wiki API", len(vehicles))
    return vehicles
