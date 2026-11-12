"""What ``tycho check`` finds, as data that prints nothing (#61): the terminal, the app
window and ``tycho check --json`` each show the same ``Finding``s their own way.

Also the player's stored keys as the checks and the wiring read them: a key that's
missing or can't be used is ``None``, with a warning that says how to fix it.
"""

import logging
import sys
from dataclasses import dataclass
from typing import Literal

from tycho import credentials

log = logging.getLogger("tycho")

TYCHO = "tycho" if getattr(sys, "frozen", False) else "uv run tycho"
"""How to run Tycho, for messages: installed, it's `tycho` (on PATH and the Start menu)."""

type Level = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    what: str
    detail: str
    fix: str = ""
    """What to run or change to fix it, when there is one (for `tycho check --json`)."""


def key_help(name: str) -> str:
    """How a player stores a key."""
    return f"{TYCHO} key set {name}"


def stored_key(name: str, label: str, fix: str) -> str | None:
    """The player's stored key, or None if there's none or it can't be used."""
    try:
        key = credentials.get(name)
    except credentials.CredentialStoreError as e:
        log.warning("Couldn't read your %s key: %s", label, e)
        return None
    if key is not None and not credentials.usable(key):
        # A pasted zero-width space, say: it can't go in a request header, and would stop
        # Tycho starting (#246, #248). Keys stored before `tycho key set` refused these.
        log.warning("Your %s key %s: %s", label, credentials.UNUSABLE, fix)
        return None
    return key


def unusable_key(name: str) -> bool:
    """Whether a key is stored but can't be used, so a check says so, not "no key" (#250)."""
    try:
        key = credentials.get(name)
    except credentials.CredentialStoreError:
        return False
    return key is not None and not credentials.usable(key)


def key_finding(name: str, what: str, label: str, without: str, fix: str) -> Finding:
    """The warning for a missing key, or for a stored one Tycho can't use (#250)."""
    if unusable_key(name):
        detail = (
            f"your {label} key is stored but unusable: it has a character keys don't have, "
            f"maybe an invisible one, so {without}. Store it again: {fix}"
        )
        return Finding("warn", what, detail, fix=fix)
    return Finding("warn", what, f"no {label} key, so {without}: {fix}", fix=fix)
