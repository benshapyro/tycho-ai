"""Draw the cards the brain eval's cases show, to review how they look with no game.

``tycho_eval.py --cards DIR`` saves each card as the Tile would draw it: a PNG and a
line in ``cards.jsonl`` (the card's fields, what was heard, what was said), in a new
folder under DIR, the same files the Tile's own record saves in play
(``tycho.tile.record``, ``TYCHO_TILE_RECORD``). The cards share one board, a card every
half minute, so the ones before stay as strips, as they would in play.
"""

from pathlib import Path

from tycho.tile import Board, Card
from tycho.tile.draw import Look, draw
from tycho.tile.record import CardRecord

BETWEEN = 30.0
"""Seconds between one case's card and the next, on the board's clock."""


class CardDrawer:
    def __init__(self, folder: Path, look: Look | None = None) -> None:
        self._now = 0.0
        self._board = Board(clock=lambda: self._now)
        self._look = look or Look()
        self._record = CardRecord(folder, background=False)

    @property
    def run(self) -> Path | None:
        """This run's folder, once a card has been saved."""
        return self._record.run

    def save(self, card: Card, *, asked: str, said: str) -> None:
        self._now += BETWEEN
        self._board.show(card)
        self._record.shown(card, asked=asked, said=said)
        view = self._board.view()
        self._record.drawn(view, draw(view, self._look, now=self._now + 1.0), self._look)
