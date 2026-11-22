"""A pack's ``[callouts]`` table (ADR 14's amendment of 22 November 2026, #4): the kinds
of game event Tycho may tell the player about without being asked, each with its fixed
lines. Out of ``pack.py`` (ADR 19's line limit).

Each kind follows one or more kinds of thing the game's state part records (``Logged``'s
``kind``), with a line for each. A line may hold ``{name}``: the one short name the log
gave (a jurisdiction's), cleaned and cut. Nothing else from the log is ever said.

A pack offers only kinds the game doesn't announce aloud itself, so a callout never
repeats the game or talks over its own voice (the amendment's "not when the game
announces the same thing itself"). Star Citizen's kinds are HUD notifications and the
end of a quantum jump; the PC check listens for the game saying any of them aloud.
"""

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

_ID = re.compile(r"[a-z][a-z0-9_]{0,30}")
LINE_LIMIT = 80
"""The longest line a callout may say: a few words, heard once (voice and tone)."""
NAME_LIMIT = 40


@dataclass(frozen=True, slots=True)
class Callout:
    id: str
    """What the ``callouts`` setting names, e.g. ``jurisdiction``."""
    name: str
    """What the window and the brain call it, e.g. "Jurisdiction changes"."""
    lines: Mapping[str, str]
    """The line for each kind of logged event it follows, by that kind."""
    example: str
    """A line as the player would hear it, for the window's settings."""


def read(table: Mapping[str, object], where: str) -> tuple[Callout, ...]:
    """The ``[callouts]`` table checked; ``ValueError`` says what's wrong with it."""
    out: list[Callout] = []
    followed: dict[str, str] = {}
    for kind, value in table.items():
        if not _ID.fullmatch(kind):
            raise ValueError(f"{where}: callout ids are lower case words joined by _: {kind}")
        if not isinstance(value, dict):
            raise ValueError(f"{where}: [callouts.{kind}] must be a table")
        callout = _callout(kind, cast(dict[str, object], value), f"{where} [callouts.{kind}]")
        for logged in callout.lines:
            if logged in followed:
                raise ValueError(f"{where}: {logged} is followed by {followed[logged]} and {kind}")
            followed[logged] = kind
        out.append(callout)
    return tuple(out)


def _callout(kind: str, table: dict[str, object], where: str) -> Callout:
    if unknown := sorted(set(table) - {"name", "lines", "example"}):
        raise ValueError(f"{where}: unknown keys: {', '.join(unknown)}")
    name = table.get("name")
    if not (isinstance(name, str) and name.strip() and len(name) <= NAME_LIMIT):
        raise ValueError(f"{where}: needs a name of up to {NAME_LIMIT} characters")
    lines = table.get("lines")
    if not (isinstance(lines, dict) and lines):
        raise ValueError(f"{where}: lines must be a table of a line for each logged kind")
    lines = cast(dict[str, object], lines)
    for logged, line in lines.items():
        _check_line(line, f"{where} lines.{logged}")
    first = next(iter(lines.values()))
    example = table.get("example", first)
    _check_line(example, f"{where} example")
    if "{name}" in cast(str, example):
        raise ValueError(f"{where}: the example is said as written, so it has no {{name}}")
    return Callout(
        kind, name.strip(), MappingProxyType(cast(dict[str, str], lines)), cast(str, example)
    )


def _check_line(line: object, where: str) -> None:
    if not (isinstance(line, str) and line.strip() and len(line) <= LINE_LIMIT):
        raise ValueError(f"{where}: needs a line of up to {LINE_LIMIT} characters")
    fields = {field for _, field, _, _ in string.Formatter().parse(line) if field is not None}
    if fields - {"name"}:
        raise ValueError(f"{where}: a line may hold only {{name}}")
