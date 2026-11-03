"""Is there a newer Tycho? One small request to GitHub's releases API (#9).

Tycho never updates itself: it says a newer version is out and where to get it, and
the player installs it. Anything that goes wrong (offline, GitHub down, rate limited,
no releases yet) means no message, never an error: it mustn't stop Tycho starting.
Turn it off with ``check_for_updates = false``.
"""

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2

log = logging.getLogger(__name__)

REPO = "benshapyro/tycho-ai"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = f"Tycho (+https://github.com/{REPO})"
TIMEOUT_SECONDS = 5.0

_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


class UpdateError(Exception):
    """GitHub couldn't be asked, or answered with something unexpected."""


@dataclass(frozen=True)
class Release:
    version: str
    url: str


def version_key(text: str) -> tuple[int, ...] | None:
    """``"v1.2.3"`` or ``"1.2.3+local"`` -> ``(1, 2, 3)``. Pre-releases (``1.2.0rc1``,
    ``1.2.0-beta``) and anything else -> None, so they're never offered."""
    match = _VERSION.fullmatch(text.strip().split("+")[0])
    if match is None:
        return None
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # 1.2 == 1.2.0
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    new, old = version_key(latest), version_key(current)
    return new is not None and old is not None and new > old


def latest_json(url: str) -> Any:
    """The JSON at ``url``, or None for 404 (no release published yet)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    try:
        with httpx2.Client(headers=headers, timeout=TIMEOUT_SECONDS) as client:
            response = client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
    except (httpx2.HTTPError, ValueError) as e:
        raise UpdateError(str(e) or type(e).__name__) from e


def latest_release(get: Callable[[str], Any] = latest_json) -> Release | None:
    """The latest published release, or None if there isn't one yet."""
    data = get(LATEST_URL)
    if data is None:
        return None
    try:
        if data.get("draft") or data.get("prerelease"):
            return None
        return Release(version=str(data["tag_name"]).lstrip("v"), url=str(data["html_url"]))
    except (AttributeError, KeyError) as e:
        raise UpdateError(f"unexpected answer from GitHub: {e}") from e


def newer_release(current: str, get: Callable[[str], Any] = latest_json) -> Release | None:
    """The latest release if it's newer than ``current``; else None."""
    release = latest_release(get)
    return release if release is not None and is_newer(release.version, current) else None


def announce_in_background(
    current: str, get: Callable[[str], Any] = latest_json, say: Callable[[str], None] = log.warning
) -> threading.Thread:
    """Check without holding up startup, and say so if there's a newer Tycho."""

    def check() -> None:
        try:
            release = newer_release(current, get)
        except UpdateError as e:
            log.debug("Couldn't check for a newer Tycho: %s", e)
            return
        if release is not None:
            say(f"Tycho {release.version} is out (you have {current}): {release.url}")

    thread = threading.Thread(target=check, name="tycho-update-check", daemon=True)
    thread.start()
    return thread
