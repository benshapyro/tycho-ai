"""Checking a key with its provider before Tycho stores it (ADR 17): an outside service,
so a ``Protocol`` with a fake in ``tests/fakes``.

One free request each: Anthropic's model list, ElevenLabs' account. Nothing is logged,
and the key goes nowhere but its own provider.
"""

from collections.abc import Callable, Mapping
from typing import Protocol

import httpx2

TIMEOUT = 8.0


class KeyCheck(Protocol):
    def check(self, name: str, key: str) -> str:
        """Empty if the key works; otherwise what's wrong, in plain words."""
        ...


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...


type Get = Callable[..., _Response]


class ProviderKeyCheck:
    """Asks the key's own provider. Satisfies ``KeyCheck``."""

    def __init__(self, get: Get = httpx2.get) -> None:
        self._get = get

    def check(self, name: str, key: str) -> str:
        request: Mapping[str, tuple[str, dict[str, str]]] = {
            "anthropic": (
                "https://api.anthropic.com/v1/models?limit=1",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
            ),
            "elevenlabs": ("https://api.elevenlabs.io/v1/user", {"xi-api-key": key}),
        }
        if name not in request:
            return "that isn't a key Tycho uses"
        url, headers = request[name]
        try:
            response = self._get(url, headers=headers, timeout=TIMEOUT)
        except httpx2.HTTPError:
            return (
                "Couldn't reach the provider to check the key. Check your connection and try again."
            )
        who = "Anthropic" if name == "anthropic" else "ElevenLabs"
        refused = response.status_code in (401, 403)
        if refused and name == "elevenlabs" and "permission" in response.text:
            return ""  # a key limited to speech: valid, just not allowed to read the account
        if refused:
            return f"{who} refused that key. Copy it again, or make a new one."
        if response.status_code >= 400:
            return f"{who} answered with an error ({response.status_code}). Try again in a moment."
        return ""
