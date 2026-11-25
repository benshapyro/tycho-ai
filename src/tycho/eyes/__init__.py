"""Tycho's eyes: one picture of the game window, only when the player asks (ADR 14, #31).

The picture is kept in memory, sent to the brain with that one request, and dropped:
never written to disk, never kept for follow-ups, and never put in the log, the brain
traces, the action log or a crash report. (The one exception is a review tool that's off
for players: ``eyes.record``, ``TYCHO_ROUTE_RECORD``.) ``Picture`` keeps its bytes out of its own
``repr`` so a stray log line can't hold them either.

Taking one is a boundary (``runtime.Camera``, with a fake in ``tests/fakes``); the
Windows Graphics Capture one is ``eyes.windows`` (ADR 3: the game window only).
"""

import io
import math
from dataclasses import dataclass, field

from PIL import Image

LONGEST = 1568
"""The longest side Claude reads a picture at without shrinking it itself."""
MOST_PIXELS = 1_150_000
"""About the most pixels Claude reads at full size: more costs tokens and time, not detail."""
QUALITY = 80
"""JPEG quality: the game's text stays readable, and a 1080p frame is about 150 KB."""


class CaptureError(Exception):
    """No picture: the game window isn't open, or Windows wouldn't capture it. The
    message says why, in words for the player's log."""


@dataclass(frozen=True, slots=True)
class Picture:
    """One frame of the game window, made small enough to send."""

    data: bytes = field(repr=False)
    """JPEG bytes. Never logged, never written to disk."""
    width: int
    height: int
    media_type: str = "image/jpeg"

    @property
    def size(self) -> int:
        """How many bytes it is: all the brain traces record about a picture (#282)."""
        return len(self.data)


def shrink(image: Image.Image) -> Picture:
    """``image`` scaled down to what the model reads (never up), as a JPEG."""
    width, height = image.size
    if width < 1 or height < 1:
        raise CaptureError("the game window has no size (is it minimised?)")
    scale = min(1.0, LONGEST / max(width, height), math.sqrt(MOST_PIXELS / (width * height)))
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=QUALITY)
    return Picture(out.getvalue(), image.width, image.height)
