"""Drawing the Tile (ADR 16): the board's cards as one see-through image, with Pillow.

The window shows what this returns and nothing else, so everything the player sees
is decided here and tested as pixels, with no screen. The image is ordinary RGBA
(straight alpha); the window premultiplies it for Windows.

Pillow draws shapes without anti-aliasing, so each card is drawn at twice its size and
scaled down, which smooths the cut corners, the ring and the text alike. A card's
image is kept until it changes: only the timer bar, the countdown and the talk light
are drawn again frame by frame.

Sizes are for medium text on a 1080-pixel-high screen; ``Look.scale`` grows them.
The fonts are Windows' own (Bahnschrift, Consolas), on every Windows 10 and 11 PC.
"""

import contextlib
import functools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from tycho.tile.board import Shown
from tycho.tile.cards import Card
from tycho.tile.design import CORE, TYCHO_ACCENT

type Light = Literal["listening", "thinking"] | None
type Rgba = tuple[int, int, int, int]
type Font = ImageFont.FreeTypeFont

SS = 2
"""Supersampling: drawn this many times bigger, then scaled down."""
WIDTH = 400
"""The Tile's width at medium text on a 1080p screen, in pixels."""
MAX_CARD = 430
"""The tallest a card may be; a long answer is cut to fit, with an ellipsis."""
GAP = 10
LIGHT = 16
"""The talk light's size, and the space above the cards it sits in."""


@dataclass(frozen=True, slots=True)
class Look:
    """How the player set the Tile up (ADR 16, #2)."""

    accent: str = TYCHO_ACCENT
    scale: float = 1.0
    """Text size times the screen's size against 1080p."""
    see_through: int = 20
    """How see-through a card's background is, in percent. Text never fades."""
    high_contrast: bool = False
    left: bool = False
    """In a left-hand corner: the talk light sits on the left."""
    bottom: bool = False
    """In a bottom corner: the newest card is at the bottom, nearest the corner."""


def rgba(hex_colour: str, alpha: float = 1.0) -> Rgba:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(255 * alpha)


# --- fonts ----------------------------------------------------------------------

type Face = Literal["label", "text", "strong", "number"]
_FACES: dict[Face, tuple[str, str | None]] = {
    "label": ("bahnschrift.ttf", "SemiBold Condensed"),
    "text": ("bahnschrift.ttf", "SemiLight SemiCondensed"),
    "strong": ("bahnschrift.ttf", "SemiBold SemiCondensed"),
    "number": ("consola.ttf", None),
}


@functools.cache
def font(face: Face, size: int) -> Font:
    """A Windows font at ``size`` pixels, or Pillow's own if it's missing."""
    file, style = _FACES[face]
    try:
        loaded = ImageFont.truetype(file, size)
    except OSError:
        fallback = ImageFont.load_default(size)  # FreeType's own, built into Pillow
        if not isinstance(fallback, ImageFont.FreeTypeFont):
            raise RuntimeError("Pillow was built without FreeType") from None
        return fallback
    if style is not None:
        # An older Bahnschrift without variations keeps its default style.
        with contextlib.suppress(OSError, ValueError):
            loaded.set_variation_by_name(style)
    return loaded


def _width(text: str, f: Font, tracking: float = 0.0) -> float:
    return f.getlength(text) + tracking * max(0, len(text) - 1)


def _wrap(text: str, f: Font, width: float, lines: int = 99) -> list[str]:
    """``text`` in lines no wider than ``width``; the last cut with an ellipsis."""
    out: list[str] = []
    line = ""
    for word in text.split():
        while _width(word, f) > width:  # a word longer than the line: break it
            cut = max(1, int(len(word) * width / _width(word, f)) - 1)
            if line:
                out.append(line)
                line = ""
            out.append(word[:cut])
            word = word[cut:]
        candidate = f"{line} {word}".strip()
        if _width(candidate, f) <= width:
            line = candidate
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = _fit(out[-1] + "…", f, width)
    return out


def _fit(text: str, f: Font, width: float, tracking: float = 0.0) -> str:
    """``text`` cut with an ellipsis to fit ``width``."""
    if _width(text, f, tracking) <= width:
        return text
    while text and _width(text + "…", f, tracking) > width:
        text = text[:-1]
    return text.rstrip() + "…"


# --- one card -------------------------------------------------------------------


class _Pen:
    """Draws one card top to bottom at the supersampled size, keeping its place."""

    def __init__(self, card: Card, look: Look) -> None:
        self.card = card
        self.look = look
        self.u = look.scale * SS
        self.w = round(WIDTH * self.u)
        self.pad = round(18 * self.u)
        self.inner = self.w - 2 * self.pad
        self.image = Image.new("RGBA", (self.w, round((MAX_CARD + 200) * self.u)), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.image)
        self.y = round(14 * self.u)
        tone = card.tone
        hc = look.high_contrast
        self.accent = rgba(look.accent)
        self.line = {
            "normal": rgba(look.accent, 1.0 if hc else 0.55),
            "caution": rgba(CORE.caution, 1.0 if hc else 0.7),
            "danger": rgba(CORE.danger, 1.0 if hc else 0.75),
        }[tone]
        self.kick = {
            "normal": self.accent,
            "caution": rgba(CORE.caution),
            "danger": rgba(CORE.danger),
        }[tone]
        self.text = rgba("#ffffff") if hc else rgba(CORE.text)
        self.dim = rgba("#c8d2e0") if hc else rgba(CORE.dim)
        self.well = (255, 255, 255, 18 if hc else 9)

    def px(self, n: float) -> int:
        return round(n * self.u)

    def caps(self, xy: tuple[float, float], text: str, f: Font, fill: Rgba, track: float) -> float:
        """Upper-case text with letter spacing (``track`` em); returns its width."""
        x, y = xy
        size = f.size
        for ch in text.upper():
            self.d.text((x, y), ch, font=f, fill=fill)
            x += f.getlength(ch) + track * size
        return x - xy[0]

    # The parts every card has.

    def kicker(self) -> None:
        f = font("label", self.px(14))
        g = self.px(14)
        self.glyph(self.pad, self.y + self.px(1), g, self.kick)
        x = self.pad + g + self.px(8)
        meta = self.card.source
        meta_w = 0.0
        if meta:
            meta = _fit(meta.upper(), f, self.inner * 0.45, 0.1 * f.size)
            meta_w = _width(meta, f, 0.1 * f.size)
            self.caps((self.pad + self.inner - meta_w, self.y), meta, f, self.dim, 0.1)
        room = self.inner - (x - self.pad) - meta_w - self.px(10)
        label = _fit((self.card.label or self.card.kind).upper(), f, room, 0.12 * f.size)
        self.caps((x, self.y), label, f, self.kick, 0.12)
        self.y += self.px(22)

    def title(self) -> None:
        if not self.card.title:
            return
        f = font("label", self.px(25))
        for line in _wrap(self.card.title.upper(), f, self.inner, lines=2):
            self.caps((self.pad, self.y), line, f, self.text, 0.05)
            self.y += self.px(27)
        self.y += self.px(6)

    def glyph(self, x: float, y: float, size: float, fill: Rgba) -> None:
        """Tycho's star: on every card, so it never looks like the game's own."""
        k = size / 24
        star = [
            (12, 1.5),
            (13.6, 10.4),
            (22.5, 12),
            (13.6, 13.6),
            (12, 22.5),
            (10.4, 13.6),
            (1.5, 12),
            (10.4, 10.4),
        ]
        self.d.polygon([(x + a * k, y + b * k) for a, b in star], fill=fill)
        r = 6.2 * k
        ring = (*fill[:3], round(fill[3] * 0.7))
        self.d.ellipse(
            (x + 12 * k - r, y + 12 * k - r, x + 12 * k + r, y + 12 * k + r),
            outline=ring,
            width=max(1, round(1.1 * k)),
        )

    # The body, by kind.

    def rows(self) -> None:
        fleet = self.card.kind == "fleet"
        best_first = self.card.kind in ("list", "fleet")
        name_f, value_f, small_f = (
            font("strong", self.px(18)),
            font("number", self.px(19)),
            font("text", self.px(14)),
        )
        for i, row in enumerate(self.card.rows):
            best = best_first and i == 0 and not row.dim
            top = self.y
            h = self.px(30 if not (fleet and row.detail) else 46)
            fill = rgba(self.look.accent, 0.14) if best else self.well
            self.d.rectangle((self.pad, top, self.pad + self.inner, top + h), fill=fill)
            if best:
                self.d.rectangle(
                    (self.pad, top, self.pad + self.px(2) - 1, top + h), fill=self.accent
                )
            alpha = 0.6 if row.dim else 1.0
            text = (*self.text[:3], round(255 * alpha))
            value_c = self.accent if best else (*self.text[:3], round(255 * alpha))
            x = self.pad + self.px(10)
            if fleet:
                rank = str(i + 1)
                self.d.text(
                    (x, top + self.px(5)),
                    rank,
                    font=value_f,
                    fill=(*self.accent[:3], round(255 * alpha)),
                )
                x += value_f.getlength(rank) + self.px(8)
            value_w = _width(row.value, value_f)
            room = self.pad + self.inner - self.px(10) - value_w - self.px(12) - x
            self.d.text((x, top + self.px(5)), _fit(row.text, name_f, room), font=name_f, fill=text)
            if fleet and row.detail:
                self.d.text(
                    (x, top + self.px(26)),
                    _fit(row.detail, small_f, room),
                    font=small_f,
                    fill=(*self.dim[:3], round(255 * alpha)),
                )
            self.d.text(
                (self.pad + self.inner - self.px(10) - value_w, top + self.px(5)),
                row.value,
                font=value_f,
                fill=value_c,
            )
            self.y += h + self.px(4)
        self.y += self.px(2)

    def recipe(self) -> None:
        name_f, value_f = font("text", self.px(18)), font("number", self.px(18))
        for row in self.card.rows:
            top, h = self.y, self.px(28)
            self.d.rectangle((self.pad, top, self.pad + self.inner, top + h), fill=self.well)
            value_w = _width(row.value, value_f)
            room = self.inner - self.px(20) - value_w - self.px(12)
            self.d.text(
                (self.pad + self.px(10), top + self.px(4)),
                _fit(row.text, name_f, room),
                font=name_f,
                fill=self.text,
            )
            self.d.text(
                (self.pad + self.inner - self.px(10) - value_w, top + self.px(4)),
                row.value,
                font=value_f,
                fill=self.accent,
            )
            self.y += h + self.px(4)
        self.y += self.px(2)

    def route(self) -> None:
        small, place, price = (
            font("label", self.px(13)),
            font("strong", self.px(18)),
            font("number", self.px(16)),
        )
        arrow_w = self.px(26)
        leg_w = (self.inner - arrow_w) / 2
        h = self.px(70)
        for i, row in enumerate(self.card.rows[:2]):
            x = self.pad + i * (leg_w + arrow_w)
            self.d.rectangle((x, self.y, x + leg_w, self.y + h), fill=self.well)
            word = "Sell" if i else "Buy"
            self.caps((x + self.px(8), self.y + self.px(6)), word, small, self.dim, 0.1)
            self.d.text(
                (x + self.px(8), self.y + self.px(23)),
                _fit(row.text, place, leg_w - self.px(16)),
                font=place,
                fill=self.text,
            )
            self.d.text(
                (x + self.px(8), self.y + self.px(46)),
                _fit(row.value, price, leg_w - self.px(16)),
                font=price,
                fill=self.text,
            )
        mid = self.pad + leg_w + arrow_w / 2
        arrow = font("number", self.px(20))
        self.d.text(
            (mid - arrow.getlength("→") / 2, self.y + h / 2 - self.px(12)),
            "→",
            font=arrow,
            fill=self.accent,
        )
        self.y += h + self.px(8)
        self.hero()

    def hero(self) -> None:
        if self.card.hero is None:
            return
        label, value = self.card.hero
        self.d.line(
            (self.pad, self.y, self.pad + self.inner, self.y),
            fill=(255, 255, 255, 20),
            width=max(1, self.px(1)),
        )
        self.y += self.px(8)
        big, small = font("number", self.px(38)), font("label", self.px(14))
        value_w = _width(value, big)
        self.caps(
            (self.pad, self.y + self.px(16)),
            _fit(label, small, self.inner - value_w - self.px(12)),
            small,
            self.dim,
            0.08,
        )
        self.d.text((self.pad + self.inner - value_w, self.y), value, font=big, fill=self.accent)
        self.y += self.px(42)

    def specs(self) -> None:
        small, value_f = font("label", self.px(12)), font("number", self.px(20))
        col = (self.inner - self.px(5)) / 2
        h = self.px(48)
        for i, row in enumerate(self.card.rows):
            x = self.pad + (i % 2) * (col + self.px(5))
            top = self.y + (i // 2) * (h + self.px(5))
            self.d.rectangle((x, top, x + col, top + h), fill=self.well)
            self.caps(
                (x + self.px(8), top + self.px(5)),
                _fit(row.text, small, col - self.px(16), 0.1 * small.size),
                small,
                self.dim,
                0.1,
            )
            shown = row.value or row.detail
            self.d.text(
                (x + self.px(8), top + self.px(21)),
                _fit(shown, value_f, col - self.px(16)),
                font=value_f,
                fill=self.text,
            )
        self.y += math.ceil(len(self.card.rows) / 2) * (h + self.px(5)) + self.px(3)

    def steps(self) -> None:
        f, n = font("text", self.px(18)), font("number", self.px(18))
        gutter = self.px(26)
        for i, row in enumerate(self.card.rows):
            self.d.text((self.pad, self.y), str(i + 1), font=n, fill=self.accent)
            for line in _wrap(row.text, f, self.inner - gutter, lines=2):
                self.d.text((self.pad + gutter, self.y), line, font=f, fill=self.text)
                self.y += self.px(23)
            self.y += self.px(5)

    def headlines(self) -> None:
        f, n = (
            font(
                "text",
                self.px(17),
            ),
            font("number", self.px(15)),
        )
        date_w = max((_width(r.value, n) for r in self.card.rows), default=0) + self.px(12)
        for row in self.card.rows:
            self.d.text((self.pad, self.y + self.px(2)), row.value, font=n, fill=self.accent)
            for line in _wrap(row.text, f, self.inner - date_w, lines=2):
                self.d.text((self.pad + date_w, self.y), line, font=f, fill=self.text)
                self.y += self.px(22)
            self.y += self.px(5)

    def words(self, size: int = 18, lines: int = 6) -> None:
        f = font("text", self.px(size))
        for paragraph in self.card.body:
            for line in _wrap(paragraph, f, self.inner, lines=lines):
                self.d.text((self.pad, self.y), line, font=f, fill=self.text)
                self.y += self.px(size * 1.3)
            self.y += self.px(6)

    def confirm(self) -> None:
        """The question, beside room for the countdown ring (drawn per frame)."""
        ring = self.px(54)
        big, f = font("label", self.px(32)), font("text", self.px(16))
        x = self.pad + ring + self.px(16)
        question = self.card.body[0] if self.card.body else ""
        asked, _, rest = question.partition("?")
        for line in _wrap((asked + "?").upper(), big, self.inner - ring - self.px(16), lines=2):
            self.caps((x, self.y), line, big, self.text, 0.06)
            self.y += self.px(34)
        hint = rest.strip() or 'Say "confirm" or "cancel".'
        for line in _wrap(hint, f, self.inner - ring - self.px(16), lines=2):
            self.d.text((x, self.y), line, font=f, fill=self.dim)
            self.y += self.px(21)
        self.y = max(self.y, self.px(36) + ring) + self.px(4)

    def body(self) -> None:
        kind = self.card.kind
        if kind in ("list", "fleet"):
            self.rows()
        elif kind == "recipe":
            self.recipe()
        elif kind == "route":
            self.route()
        elif kind == "ship":
            self.specs()
        elif kind == "steps":
            self.steps()
        elif kind == "headlines":
            self.headlines()
        elif kind == "confirm":
            self.confirm()
        elif kind == "long":
            self.words(17, lines=14)
        else:  # warning, notice
            self.words()

    def finish(self) -> Image.Image:
        """Cut to height, frame it, and scale down."""
        h = min(self.y + self.px(10), self.px(MAX_CARD))
        content = self.image.crop((0, 0, self.w, h))
        out = Image.new("RGBA", (self.w, h), (0, 0, 0, 0))
        frame = ImageDraw.Draw(out)
        c = self.px(14)
        shape = [
            (c, 0),
            (self.w - 1, 0),
            (self.w - 1, h - 1 - c),
            (self.w - 1 - c, h - 1),
            (0, h - 1),
            (0, c),
        ]
        glass = 1.0 if self.look.high_contrast else 1 - self.look.see_through / 100
        frame.polygon(shape, fill=rgba(CORE.glass, glass))
        out.alpha_composite(content)
        frame.line([*shape, shape[0]], fill=self.line, width=max(1, self.px(1.2)), joint="curve")
        return out.resize((round(self.w / SS), round(h / SS)), Image.Resampling.LANCZOS)


@functools.lru_cache(maxsize=16)
def card_image(card: Card, look: Look) -> Image.Image:
    """One card, drawn: kept, since a card doesn't change once shown."""
    pen = _Pen(card, look)
    pen.kicker()
    pen.title()
    pen.body()
    return pen.finish()


@functools.lru_cache(maxsize=8)
def strip_image(card: Card, look: Look) -> Image.Image:
    """A pinned card that gave way to a newer one: its title, to bring back."""
    u = look.scale * SS
    w, h = round(WIDTH * u), round(34 * u)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    c = round(8 * u)
    shape = [(c, 0), (w - 1, 0), (w - 1, h - 1 - c), (w - 1 - c, h - 1), (0, h - 1), (0, c)]
    glass = 1.0 if look.high_contrast else 1 - look.see_through / 100
    d.polygon(shape, fill=rgba(CORE.glass, glass))
    d.line(
        [*shape, shape[0]],
        fill=rgba(look.accent, 0.55),
        width=max(1, round(1.2 * u)),
        joint="curve",
    )
    pen = _Pen(card, look)
    pen.d = d
    pen.glyph(round(12 * u), round(10 * u), round(14 * u), rgba(look.accent))
    f = font("label", round(15 * u))
    title = _fit(
        (card.title or card.label or card.kind).upper(), f, w - round(70 * u), 0.08 * f.size
    )
    pen.caps((round(34 * u), round(8 * u)), title, f, rgba(CORE.dim), 0.08)
    arrow = font("number", round(16 * u))
    d.text((w - round(26 * u), round(7 * u)), "▸", font=arrow, fill=rgba(look.accent))
    return out.resize((round(w / SS), round(h / SS)), Image.Resampling.LANCZOS)


# --- what moves -----------------------------------------------------------------


def _timebar(image: Image.Image, left: float, look: Look) -> None:
    """The line along a card's foot that shrinks as it fades: ``left`` from 1 to 0."""
    d = ImageDraw.Draw(image)
    h = max(1, round(2 * look.scale))
    d.rectangle(
        (0, image.height - h, round(image.width * max(0.0, left)), image.height - 1),
        fill=rgba(look.accent),
    )


def _ring(image: Image.Image, left: float, seconds: float, look: Look) -> None:
    """The countdown beside a question: a red ring emptying, and the seconds left."""
    u = look.scale
    size = round(54 * u * SS)
    patch = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(patch)
    width = round(4 * u * SS)
    box = (width, width, size - width, size - width)
    d.ellipse(box, outline=(255, 255, 255, 34), width=width)
    if left > 0:
        d.arc(box, -90, -90 + 360 * left, fill=rgba(CORE.danger), width=width)
    n = font("number", round(20 * u * SS))
    secs = str(math.ceil(left * seconds))
    d.text(
        (size / 2 - n.getlength(secs) / 2, size / 2 - round(12 * u * SS)),
        secs,
        font=n,
        fill=rgba(CORE.text),
    )
    side = round(size / SS)
    patch = patch.resize((side, side), Image.Resampling.LANCZOS)
    image.alpha_composite(patch, (round(18 * u), round(38 * u)))


def _light(size: int, light: Light, phase: float, look: Look) -> Image.Image:
    """The talk light: a diamond, no text. Listening, it pulses green; thinking, it fades
    in and out in the accent. They differ in motion as well as colour."""
    out = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    if light is None:
        return out.resize((size, size))
    d = ImageDraw.Draw(out)
    s = size * SS
    if light == "listening":
        grow = 0.75 + 0.25 * (1 - phase)  # a ping: big, then settling
        colour = rgba(CORE.good)
        halo = rgba(CORE.good, 0.45 * (1 - phase))
        r = s / 2
        d.polygon([(r, 0), (s, r), (r, s), (0, r)], fill=halo)
    else:
        grow = 0.75
        colour = rgba(look.accent, 0.25 + 0.75 * (0.5 + 0.5 * math.cos(2 * math.pi * phase)))
    r = s / 2 * grow
    c = s / 2
    d.polygon([(c, c - r), (c + r, c), (c, c + r), (c - r, c)], fill=colour)
    return out.resize((size, size), Image.Resampling.LANCZOS)


def draw(
    view: Sequence[Shown],
    look: Look,
    *,
    now: float,
    light: Light = None,
    phase: float = 0.0,
) -> Image.Image:
    """The whole Tile: the talk light, then the cards, newest nearest the corner.

    ``now`` is the board's clock, for the timer bars and countdowns; ``phase`` (0 to 1)
    is where the talk light is in its pulse. An empty board with no light is a fully
    transparent image: the Tile is invisible.
    """
    w = round(WIDTH * look.scale)
    light_size = round(LIGHT * look.scale)
    parts: list[Image.Image] = []
    for shown in view:
        if shown.strip:
            parts.append(strip_image(shown.card, look))
            continue
        image = card_image(shown.card, look).copy()
        card = shown.card
        if card.kind == "confirm" and shown.until is not None and card.seconds:
            left = max(0.0, (shown.until - now) / card.seconds)
            _ring(image, left, card.seconds, look)
        elif shown.until is not None and card.kind != "confirm":
            total = shown.until - shown.since
            _timebar(image, (shown.until - now) / total if total > 0 else 0.0, look)
        parts.append(image)
    if look.bottom:
        parts.reverse()
    gap = round(GAP * look.scale)
    height = light_size + gap + sum(p.height for p in parts) + gap * max(0, len(parts) - 1)
    out = Image.new("RGBA", (w, max(height, light_size)), (0, 0, 0, 0))
    y = 0
    light_y = out.height - light_size if look.bottom else 0
    if not look.bottom:
        y = light_size + gap
    for part in parts:
        out.alpha_composite(part, (0, y))
        y += part.height + gap
    if light is not None:
        lx = 0 if look.left else w - light_size
        out.alpha_composite(_light(light_size, light, phase, look), (lx, light_y))
    return out
