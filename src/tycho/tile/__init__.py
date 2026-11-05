"""The Tile (#2, ADR 16): a small see-through panel over the game, for what the eye
handles better than the ear. The design system, the card kit, and the rules for what
shows; the drawing and the window come separately, behind a ``Display`` boundary."""

from tycho.tile.board import Board, Shown, lifetime
from tycho.tile.cards import BRAIN_KINDS, Card, Kind, Row, from_tool

__all__ = ["BRAIN_KINDS", "Board", "Card", "Kind", "Row", "Shown", "from_tool", "lifetime"]
