"""The Tile's preview on its settings page: drawn by the Tile itself, over a stand-in
cockpit, as it would sit on a 2560 x 1440 screen."""

import base64
import io
from typing import TYPE_CHECKING, Any

from tycho import settings
from tycho.desktop.bridge.state import State
from tycho.packs import PackError, Sample, built_in

if TYPE_CHECKING:
    from tycho.tile.cards import Card


class TilePage(State):
    def tile_preview(self, width: int = 640) -> str:
        """The Tile as it is set now, sized and placed exactly as on a 2560 x 1440
        screen, drawn by the Tile itself and scaled to ``width``: a PNG data URL."""
        from PIL import Image

        from tycho.tile.board import Board
        from tycho.tile.design import game_accent
        from tycho.tile.display import accent, look, place
        from tycho.tile.draw import draw
        from tycho.tile.window import Screen

        current = settings.load(self._path)
        screen = Screen(0, 0, 2560, 1440)
        board = Board(clock=lambda: 0.0)
        sample = _sample(current.game) or OWN_SAMPLE
        board.show(_card(sample))
        try:
            ship = self._ship_accent()
        except Exception:
            ship = None
        image = _cockpit(screen.width, screen.height)
        if current.tile:
            tile_look = look(
                current, screen, accent(current.tile_colour, ship, game_accent(current.game))
            )
            light = "thinking" if current.tile_talk_light else None
            tile = draw(board.view(), tile_look, now=1.0, light=light)
            image.alpha_composite(tile, place(current, screen, tile.width, tile.height))
        width = max(320, min(int(width), screen.width))
        small = image.resize((width, width * 9 // 16), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        small.convert("RGB").save(buffer, "PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


OWN_SAMPLE = Sample(
    "Getting started",
    "Tycho",
    rows=(
        ("Hold your button", "and ask", "1"),
        ("Let go", "Tycho answers", "2"),
        ("Ask for more", "or say cancel", "3"),
    ),
)
"""The preview's card for a game whose pack has none: no game's words (#422)."""


def _card(sample: Sample) -> Card:
    """The sample as a card, cleaned and cut like one from the brain (review of #461)."""
    from tycho.tile.cards import (
        DEFAULT_ROWS,
        DETAIL,
        LABEL,
        SOURCE,
        TEXT,
        TITLE,
        VALUE,
        Card,
        Row,
        clean,
    )

    rows = tuple(
        Row(clean(text, TEXT), clean(detail, DETAIL), clean(value, VALUE))
        for text, detail, value in sample.rows[:DEFAULT_ROWS]
    )
    return Card(
        "list",
        title=clean(sample.title, TITLE),
        label=clean(sample.label, LABEL),
        source=clean(sample.source, SOURCE),
        rows=rows,
    )


def _sample(game: str) -> Sample | None:
    try:
        return built_in(game).tile.sample
    except PackError:
        return None


def _cockpit(width: int, height: int) -> Any:
    from PIL import Image, ImageDraw

    # Drawn on RGB so the see-through glare and ring blend (Pillow blends only onto RGB).
    image = Image.new("RGB", (width, height), (8, 12, 20))
    d = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        v = round(12 + 18 * y / height)
        d.line((0, y, width, y), fill=(v, v + 5, v + 14, 255))
    cx, cy, r = width // 2, round(height * 0.56), round(height * 0.24)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(245, 144, 30, 110), width=2)
    d.rectangle((cx - 9, cy - 9, cx + 9, cy + 9), outline=(245, 144, 30, 150), width=2)
    d.ellipse(
        (round(width * 0.66), round(height * 0.08), round(width * 0.82), round(height * 0.2)),
        fill=(225, 220, 205, 90),
    )
    return image.convert("RGBA")
