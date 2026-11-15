"""The Tile's fonts (ADR 16, ADR 17): the bundled files (``tycho.fonts``) the window
uses too, with Windows' own and then Pillow's as the fallbacks. Out of ``draw.py``."""

import contextlib
import functools
import io
from typing import Literal

from PIL import ImageFont

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


@functools.cache
def font(face: Face, size: int) -> Font:
    """The bundled font at ``size`` pixels, else Windows' own, else Pillow's."""
    with contextlib.suppress(OSError):
        return ImageFont.truetype(io.BytesIO(fonts.path(_BUNDLED[face]).read_bytes()), size)
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
