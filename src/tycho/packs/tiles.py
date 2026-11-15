"""A pack's ``tile.toml`` (ADR 16, ADR 18): the Tile's accents by maker, and the sample
card the window's preview shows (#422). Out of ``pack.py`` (ADR 19's line limit)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

_COLOUR = re.compile(r"#[0-9a-f]{6}")


@dataclass(frozen=True, slots=True)
class Sample:
    """The sample card the window's Tile preview shows for this game (#422): a list."""

    title: str
    label: str = ""
    source: str = ""
    rows: tuple[tuple[str, str, str], ...] = ()
    """Each row's text, detail and value."""


@dataclass(frozen=True, slots=True)
class Tile:
    sample: Sample | None = None
    """The preview's card in this game's words; None takes Tycho's own."""
    accents: Mapping[str, str] = field(default_factory=dict[str, str])
    """The Tile's accent by the maker prefix of a ship's game name (``DRAK``)."""
    makers: Mapping[str, str] = field(default_factory=dict[str, str])
    """A maker's name, by its first word, to its prefix (``drake`` to ``DRAK``)."""
    cards: Mapping[str, str] = field(default_factory=dict[str, str])
    """This game's own kinds of card, by the names the window gives them (#422). The
    kinds every game has are named by Tycho (``tile.cards.NAMES``)."""


def read(table: Mapping[str, object], where: str) -> Tile:
    """``tile.toml`` checked; ``ValueError`` says what's wrong with it."""
    accents = _table(table, "accents", where)
    makers = _table(table, "makers", where)
    # Each value's type first: a list as a maker's accent can't be looked up (#319).
    if bad := [k for k, v in accents.items() if not (isinstance(v, str) and _COLOUR.fullmatch(v))]:
        raise ValueError(f"{where}: accents must be colours like #4fb3ff: {', '.join(bad)}")
    if bad := [k for k, v in makers.items() if not isinstance(v, str)]:
        raise ValueError(f"{where}: each maker must name an accent: {', '.join(bad)}")
    if bad := [k for k, v in makers.items() if v not in accents]:
        raise ValueError(f"{where}: makers with no accent: {', '.join(bad)}")
    return Tile(
        _sample(_table(table, "sample", where), where),
        MappingProxyType(cast(dict[str, str], accents)),
        MappingProxyType(cast(dict[str, str], makers)),
        MappingProxyType(_cards(_table(table, "cards", where), where)),
    )


def _cards(table: Mapping[str, object], where: str) -> dict[str, str]:
    from tycho.tile.cards import BRAIN_KINDS, NAMES

    own = [k for k in BRAIN_KINDS if k not in NAMES]
    if bad := [k for k in table if k not in own]:
        raise ValueError(
            f"{where}: [cards] names only a game's own kinds ({', '.join(own)}): {', '.join(bad)}"
        )
    if bad := [k for k, v in table.items() if not (isinstance(v, str) and v.strip())]:
        raise ValueError(f"{where}: each card needs a name: {', '.join(bad)}")
    return cast(dict[str, str], dict(table))


def _sample(table: Mapping[str, object], where: str) -> Sample | None:
    if not table:
        return None
    title = table.get("title")
    if not (isinstance(title, str) and title.strip()):
        raise ValueError(f"{where}: the sample needs a title")
    label, source, rows = table.get("label", ""), table.get("source", ""), table.get("rows", [])
    if not (isinstance(label, str) and isinstance(source, str)):
        raise ValueError(f"{where}: the sample's label and source must be text")
    cells = [cast(list[object], r) for r in cast(list[object], rows) if isinstance(r, list)]
    if (
        not isinstance(rows, list)
        or len(cells) != len(cast(list[object], rows))
        or any(len(c) != 3 or not all(isinstance(x, str) for x in c) for c in cells)
    ):
        raise ValueError(f"{where}: each sample row is three pieces of text: what, where, value")
    return Sample(title, label, source, tuple((str(a), str(b), str(c)) for a, b, c in cells))


def _table(table: Mapping[str, object], key: str, where: str) -> dict[str, object]:
    value = table.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{where}: [{key}] must be a table")
    return cast(dict[str, object], value)
