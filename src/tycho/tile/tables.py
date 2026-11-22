"""The Tile's table-like cards (#491): a loadout, a comparison, and bars for numbers.

Out of ``draw.py`` (ADR 19's line limit), with the steps and headlines that moved with
them. Each function draws one card's body with the card's pen, top to bottom, and
moves the pen down past what it drew.

Bars only ever show numbers the card itself carries: they're the same figures, drawn
to compare at a glance, never a gauge Tycho can't read (ADR 16).
"""

import re
from collections.abc import Sequence
from typing import Protocol

from PIL import ImageDraw

from tycho.tile.cards import Card, Row
from tycho.tile.faces import Face, Font
from tycho.tile.faces import fit as _fit
from tycho.tile.faces import text_width as _width
from tycho.tile.faces import wrap as _wrap

type Rgba = tuple[int, int, int, int]

_NUMBER = re.compile(r"^\D*?(\d[\d,]*(?:\.\d+)?)\s*([^\d]*)$")
"""One number with its unit around it: "3,048", "72 SCU", "681.8 Gm", "S3"."""
CLOSE = 0.9
"""Bars that would all be this close to the biggest say nothing: none are drawn."""


class Pen(Protocol):
    """What these need of ``draw``'s pen: its canvas, its colours and its place."""

    card: Card
    d: ImageDraw.ImageDraw
    pad: int
    inner: int
    y: int
    accent: Rgba
    text: Rgba
    dim: Rgba
    well: Rgba

    def font(self, face: Face, size: int) -> Font: ...
    def px(self, n: float) -> int: ...
    def caps(
        self, xy: tuple[float, float], text: str, f: Font, fill: Rgba, track: float
    ) -> float: ...


def number(value: str) -> tuple[float, str] | None:
    """The one number in ``value`` and its unit, or None: "1 to 4" has two, "Large" none."""
    match = _NUMBER.match(value.strip())
    if not match:
        return None
    return float(match.group(1).replace(",", "")), match.group(2).strip().lower()


def bar_lengths(values: Sequence[str]) -> list[float] | None:
    """Each value against the biggest, 0 to 1, when every one is a number in the same
    unit and they aren't all equal; otherwise None, and no bars are drawn."""
    numbers = [number(v) for v in values]
    if len(numbers) < 2 or any(n is None for n in numbers):
        return None
    found = [n for n in numbers if n is not None]
    if len({unit for _, unit in found}) != 1:
        return None
    top = max(v for v, _ in found)
    if top <= 0 or min(v for v, _ in found) > top * CLOSE:
        return None
    return [v / top for v, _ in found]


def _fade(colour: Rgba, alpha: float) -> Rgba:
    return (*colour[:3], round(255 * alpha))


def row_bars(pen: Pen, tops: Sequence[tuple[int, int]], rows: Sequence[Row]) -> None:
    """A list's bars: a thin line along the foot of each row, as long as its value is
    against the biggest. ``tops`` is each row's top and height."""
    lengths = bar_lengths([r.value for r in rows])
    if lengths is None:
        return
    h = max(1, pen.px(2))
    for (top, height), length in zip(tops, lengths, strict=True):
        foot = top + height - h
        right = pen.pad + round(pen.inner * length)
        pen.d.rectangle((pen.pad, foot, right, foot + h - 1), fill=_fade(pen.accent, 0.7))


def loadout(pen: Pen) -> None:
    """What a ship is fitted with: each item with its size, and its slot on the right,
    said once for the rows in a row that share it."""
    slot_f, name_f, size_f = (
        pen.font("label", pen.px(13)),
        pen.font("text", pen.px(17)),
        pen.font("number", pen.px(15)),
    )
    badge_w = max((_width(r.value, size_f) for r in pen.card.rows), default=0) + pen.px(12)
    slot_room = pen.inner * 0.38
    last = None
    for row in pen.card.rows:
        top, h = pen.y, pen.px(28)
        pen.d.rectangle((pen.pad, top, pen.pad + pen.inner, top + h), fill=pen.well)
        x = pen.pad + pen.px(6)
        if row.value:
            pen.d.rectangle(
                (x, top + pen.px(5), x + badge_w, top + h - pen.px(5)),
                outline=_fade(pen.accent, 0.8),
                width=max(1, pen.px(1)),
            )
            w = _width(row.value, size_f)
            pen.d.text(
                (x + (badge_w - w) / 2, top + pen.px(6)), row.value, font=size_f, fill=pen.accent
            )
            x += badge_w + pen.px(10)
        slot_w = 0.0
        if row.detail and row.detail != last:
            slot = _fit(row.detail.upper(), slot_f, slot_room, 0.1 * slot_f.size)
            slot_w = _width(slot, slot_f, 0.1 * slot_f.size)
            right = pen.pad + pen.inner - pen.px(8) - slot_w
            pen.caps((right, top + pen.px(7)), slot, slot_f, pen.dim, 0.1)
        last = row.detail
        room = pen.pad + pen.inner - pen.px(8) - x - (slot_w + pen.px(10) if slot_w else 0)
        pen.d.text((x, top + pen.px(4)), _fit(row.text, name_f, room), font=name_f, fill=pen.text)
        pen.y += h + pen.px(3)
    pen.y += pen.px(3)


def compare(pen: Pen) -> None:
    """Two things side by side: their names on top, then each figure, left against
    right, under its name, with bars growing out from the middle when both are numbers."""
    left, right = pen.card.hero or ("", "")
    name_f, head, value_f = (
        pen.font("strong", pen.px(18)),
        pen.font("label", pen.px(12)),
        pen.font("number", pen.px(18)),
    )
    half = pen.inner / 2
    mid = pen.pad + half
    pen.d.text(
        (pen.pad, pen.y), _fit(left, name_f, half - pen.px(14)), font=name_f, fill=pen.accent
    )
    shown = _fit(right, name_f, half - pen.px(14))
    pen.d.text(
        (pen.pad + pen.inner - _width(shown, name_f), pen.y), shown, font=name_f, fill=pen.text
    )
    pen.y += pen.px(28)
    h = pen.px(44)
    bar = max(1, pen.px(3))
    for row in pen.card.rows:
        top = pen.y
        pen.d.rectangle((pen.pad, top, pen.pad + pen.inner, top + h), fill=pen.well)
        spec = _fit(row.text.upper(), head, pen.inner - pen.px(16), 0.1 * head.size)
        spec_w = _width(spec, head, 0.1 * head.size)
        pen.caps((mid - spec_w / 2, top + pen.px(4)), spec, head, pen.dim, 0.1)
        a = _fit(row.detail, value_f, half - pen.px(16))
        b = _fit(row.value, value_f, half - pen.px(16))
        pen.d.text((pen.pad + pen.px(8), top + pen.px(16)), a, font=value_f, fill=pen.text)
        b_x = pen.pad + pen.inner - pen.px(8) - _width(b, value_f)
        pen.d.text((b_x, top + pen.px(16)), b, font=value_f, fill=pen.text)
        lengths = bar_lengths([row.detail, row.value])
        if lengths is not None:
            reach = half - pen.px(8)
            foot = top + h - pen.px(6)
            left_end = mid - round(reach * lengths[0])
            right_end = mid + round(reach * lengths[1])
            pen.d.rectangle((left_end, foot, mid - pen.px(1), foot + bar - 1), fill=pen.accent)
            pen.d.rectangle(
                (mid + pen.px(1), foot, right_end, foot + bar - 1), fill=_fade(pen.text, 0.75)
            )
        pen.y += h + pen.px(4)
    pen.y += pen.px(2)


def steps(pen: Pen) -> None:
    f, n = pen.font("text", pen.px(18)), pen.font("number", pen.px(18))
    gutter = pen.px(26)
    for i, row in enumerate(pen.card.rows):
        pen.d.text((pen.pad, pen.y), str(i + 1), font=n, fill=pen.accent)
        for line in _wrap(row.text, f, pen.inner - gutter, lines=2):
            pen.d.text((pen.pad + gutter, pen.y), line, font=f, fill=pen.text)
            pen.y += pen.px(23)
        pen.y += pen.px(5)


def headlines(pen: Pen) -> None:
    f, n = pen.font("text", pen.px(17)), pen.font("number", pen.px(15))
    date_w = max((_width(r.value, n) for r in pen.card.rows), default=0) + pen.px(12)
    for row in pen.card.rows:
        pen.d.text((pen.pad, pen.y + pen.px(2)), row.value, font=n, fill=pen.accent)
        for line in _wrap(row.text, f, pen.inner - date_w, lines=2):
            pen.d.text((pen.pad + date_w, pen.y), line, font=f, fill=pen.text)
            pen.y += pen.px(22)
        pen.y += pen.px(5)
