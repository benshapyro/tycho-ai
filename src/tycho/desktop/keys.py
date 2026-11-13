"""Checking a key with its provider before Tycho stores it (ADR 17): an outside service,
so a ``Protocol`` with a fake in ``tests/fakes``.

One free request each: Anthropic's model list, OpenRouter's key details, ElevenLabs'
account. Nothing is logged,
and the key goes nowhere but its own provider.
"""

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

import httpx2

TIMEOUT = 8.0


class KeyCheck(Protocol):
    def check(self, name: str, key: str) -> str:
        """Empty if the key works; otherwise what's wrong, in plain words."""
        ...

    def credit(self, name: str, key: str) -> str:
        """Empty unless a working key has nothing left to spend (#315), then why."""
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
            "openrouter": (
                "https://openrouter.ai/api/v1/key",
                {"Authorization": f"Bearer {key}"},
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
        who = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}.get(name, "ElevenLabs")
        refused = response.status_code in (401, 403)
        if refused and name == "elevenlabs" and "permission" in response.text:
            return ""  # a key limited to speech: valid, just not allowed to read the account
        if refused:
            return f"{who} refused that key. Copy it again, or make a new one."
        if response.status_code >= 400:
            return f"{who} answered with an error ({response.status_code}). Try again in a moment."
        return ""

    def credit(self, name: str, key: str) -> str:
        """For an OpenRouter key: empty while it can spend, otherwise why not (#315).
        ``/key`` answers 200 for a key with nothing left, and every request then fails
        with 402. Two free requests: the key's own limit, and the account's credit. A
        failed or odd answer says nothing: the key check already passed."""
        if name != "openrouter":
            return ""
        headers = {"Authorization": f"Bearer {key}"}
        limit = self._data("https://openrouter.ai/api/v1/key", headers)
        remaining = limit.get("limit_remaining")
        if isinstance(remaining, int | float) and remaining <= 0:
            return "this OpenRouter key has reached its spending limit; raise it on openrouter.ai"
        credits = self._data("https://openrouter.ai/api/v1/credits", headers)
        total, used = credits.get("total_credits"), credits.get("total_usage")
        if isinstance(total, int | float) and isinstance(used, int | float) and total <= used:
            return "your OpenRouter account has no credit left; add some on openrouter.ai"
        return ""

    def _data(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._get(url, headers=headers, timeout=TIMEOUT)
            if response.status_code != 200:
                return {}
            data: object = json.loads(response.text).get("data")
        except httpx2.HTTPError, ValueError, AttributeError:
            return {}
        return cast(dict[str, Any], data) if isinstance(data, dict) else {}
