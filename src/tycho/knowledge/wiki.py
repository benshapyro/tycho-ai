"""The Star Citizen Wiki API (api.star-citizen.wiki): game data for the current patch.

Public, no key needed for reads. Its terms ask for credit in public projects and
forbid commercial use, and its data has no stated licence, so Tycho never ships a
copy: each player's PC downloads its own (ADR 15). Credit is in the README.
"""

import logging
from collections.abc import Mapping
from typing import Any, Protocol

import httpx2

log = logging.getLogger(__name__)

BASE_URL = "https://api.star-citizen.wiki"
USER_AGENT = "Tycho (+https://github.com/benshapyro/tycho-ai)"


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


async def current_version(source: WikiSource) -> str:
    """The patch the Wiki API serves by default, e.g. "4.10.2-LIVE.12881860"."""
    data = await source.get("/api/v2/game-versions")
    try:
        return next(str(v["code"]) for v in data["data"] if v.get("is_default"))
    except (KeyError, TypeError, StopIteration) as e:
        raise WikiError("no default game version") from e


async def all_vehicles(source: WikiSource) -> list[dict[str, Any]]:
    """Every ship and ground vehicle, a page at a time (about 11 requests)."""
    vehicles: list[dict[str, Any]] = []
    page, last = 1, 1
    while page <= last:
        data = await source.get("/api/v2/vehicles", {"page": page})
        try:
            vehicles += data["data"]
            last = int(data["meta"]["last_page"])
        except (KeyError, TypeError, ValueError) as e:
            raise WikiError(f"unexpected vehicles page {page}") from e
        page += 1
    log.debug("Downloaded %d vehicles from the Star Citizen Wiki API", len(vehicles))
    return vehicles
