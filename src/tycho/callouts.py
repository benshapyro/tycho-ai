"""The ``callouts`` setting (ADR 14's amendment of 22 November 2026, #4): which kinds of
game event Tycho tells the player about without being asked. Off until the player picks
kinds; the pack names the kinds its game offers (``packs.callouts``). Out of
``settings.py`` (ADR 19's line limit).
"""

import re
from collections.abc import Sequence
from typing import Annotated

from pydantic import AfterValidator

from tycho.packs.callouts import Callout

_KIND = re.compile(r"[a-z][a-z0-9_]{0,30}")
ALL = frozenset({"all", "everything", "every kind", "all of them"})
NONE = frozenset({"", "off", "none", "nothing", "no", "stop", "false", "0"})


def _kinds(value: str) -> str:
    kinds = [_id(k) for k in value.replace(";", ",").split(",") if k.strip()]
    if bad := [k for k in kinds if not _KIND.fullmatch(k)]:
        raise ValueError(f"callout kinds are words joined by _, not {', '.join(bad)}")
    return ", ".join(dict.fromkeys(kinds))


CalloutKinds = Annotated[str, AfterValidator(_kinds)]
"""Kinds of callout by id, by comma. Ids another game offers are kept, and do nothing
here: the setting is shared by every game."""


def turned_on(value: str) -> frozenset[str]:
    """The ids the setting turns on."""
    return frozenset(k.strip() for k in value.split(",") if k.strip())


def said(text: str, current: str, offered: Sequence[Callout]) -> str:
    """The setting's new value from what the brain passed for the player: "off" or
    "all", kinds by id or name, or "+kind" / "-kind" to add or take one away. Raises
    ``ValueError`` naming the kinds offered."""
    text = text.strip().lower()
    if text in NONE:
        return ""
    if text in ALL:
        return ", ".join(c.id for c in offered)
    sign = text[:1] if text[:1] in "+-" else ""
    names = [_match(k, offered) for k in text.lstrip("+-").replace(";", ",").split(",")]
    kinds = [k for k in names if k is not None]
    if None in names or not kinds:
        shown = ", ".join(f"{c.id} ({c.name})" for c in offered) or "none in this game"
        raise ValueError(f"the kinds of callout are {shown}")
    on = list(turned_on(current))
    if sign == "+":
        kinds = [*on, *kinds]
    elif sign == "-":
        kinds = [k for k in on if k not in kinds]
    order = {c.id: n for n, c in enumerate(offered)}
    return ", ".join(dict.fromkeys(sorted(kinds, key=lambda k: order.get(k, len(order)))))


def _match(word: str, offered: Sequence[Callout]) -> str | None:
    word = _id(word)
    for callout in offered:
        if word in (callout.id, _id(callout.name)):
            return callout.id
    return None


def _id(word: str) -> str:
    return "_".join(word.strip().lower().replace("-", " ").split())
