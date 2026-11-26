"""The Tile's fonts (ADR 16, ADR 17): the bundled files (``tycho.fonts``) the window
uses too, with Windows' own and then Pillow's as the fallbacks. Out of ``draw.py``.

The bundled files cover Latin, not Greek or Cyrillic.
Pillow doesn't fall back glyph by glyph as the window's browser does, so a card with a
character they lack draws in Windows' own faces instead of as boxes (#458)."""

import contextlib
import functools
import io
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from tycho import fonts

type Font = ImageFont.FreeTypeFont

type Face = Literal["label", "text", "strong", "number"]
_BUNDLED: dict[Face, str] = {
    "label": "condensed",
    "text": "text",
    "strong": "label",
    "number": "number",
}
"""Each face's bundled font (``tycho.fonts``), the same files the window uses."""

_WINDOWS: dict[Face, tuple[str, str | None]] = {
    "label": ("bahnschrift.ttf", "SemiBold Condensed"),
    "text": ("bahnschrift.ttf", "SemiLight SemiCondensed"),
    "strong": ("bahnschrift.ttf", "SemiBold SemiCondensed"),
    "number": ("consola.ttf", None),
}
"""Windows' own, if a bundled file is missing."""


_NOT_IN_ANY = "\U000f0000"
"""A private-use code point no font here has: what a missing character draws as."""


@functools.cache
def font(face: Face, size: int, windows: bool = False) -> Font:
    """The bundled font at ``size`` pixels, else (or with ``windows``) Windows' own,
    else Pillow's."""
    if not windows:
        with contextlib.suppress(OSError):
            return _bundled(face, size)
    file, style = _WINDOWS[face]
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


def _bundled(face: Face, size: int) -> Font:
    return ImageFont.truetype(io.BytesIO(fonts.path(_BUNDLED[face]).read_bytes()), size)


def bundled_draws(words: str, numbers: str = "") -> bool:
    """Whether the bundled faces have every character: ``words`` in the label and text
    faces, ``numbers`` (values, drawn in the number face) in that one. If not, the Tile
    draws the card with Windows' own faces (#458)."""
    texts: tuple[Face, ...] = ("label", "text", "strong")
    return all(_has(face, ch) for ch in _shown(words) for face in texts) and all(
        _has("number", ch) for ch in _shown(numbers)
    )


def _shown(text: str) -> set[str]:
    return {ch for ch in text if not ch.isspace()}


@functools.cache
def _has(face: Face, ch: str) -> bool:
    """Checked once per face and character, against the box a missing one draws. By its
    ink: in a monospaced face every character has the box's size."""
    try:
        f, box = _probe(face)
    except OSError:
        return True  # no bundled file: this face is Windows' own already
    return _ink(f, ch) != box


@functools.cache
def _probe(face: Face) -> tuple[Font, bytes]:
    """The bundled face at 32 px and the ink of the box it draws for a missing character,
    loaded once per face, not on each new character's check (#515)."""
    f = _bundled(face, 32)
    return f, _ink(f, _NOT_IN_ANY)


def _ink(f: Font, ch: str) -> bytes:
    image = Image.new("L", (64, 64))
    ImageDraw.Draw(image).text((8, 8), ch, font=f, fill=255)
    return image.tobytes()


# Fitting text to a width (out of draw.py, ADR 19's 500-line limit).


def text_width(text: str, f: Font, tracking: float = 0.0) -> float:
    return f.getlength(text) + tracking * max(0, len(text) - 1)


def wrap(text: str, f: Font, width: float, lines: int = 99, tracking: float = 0.0) -> list[str]:
    """``text`` in lines no wider than ``width``; the last cut with an ellipsis.
    ``tracking`` is the space added after each letter, as ``caps`` draws a title."""
    out: list[str] = []
    line = ""
    for word in text.split():
        while text_width(word, f, tracking) > width:  # a word longer than the line: break it
            cut = max(1, int(len(word) * width / text_width(word, f, tracking)) - 1)
            if line:
                out.append(line)
                line = ""
            out.append(word[:cut])
            word = word[cut:]
        candidate = f"{line} {word}".strip()
        if text_width(candidate, f, tracking) <= width:
            line = candidate
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = fit(out[-1] + "…", f, width, tracking)
    return out


def fit(text: str, f: Font, width: float, tracking: float = 0.0) -> str:
    """``text`` cut with an ellipsis to fit ``width``."""
    if text_width(text, f, tracking) <= width:
        return text
    while text and text_width(text + "…", f, tracking) > width:
        text = text[:-1]
    return text.rstrip() + "…"
