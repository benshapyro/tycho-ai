"""A record of every card the Tile shows, to look back at how they looked: a review tool.

Off for players. ``TYCHO_TILE_RECORD=1`` turns it on. Each card, the first time it's
drawn, is saved as a picture of the whole Tile as it was on screen (strips and all, on
a dark backdrop for the see-through glass), with a line in ``cards.jsonl`` beside it:
when, the card's kind and every word and number in it, what the player asked and what
Tycho said with it, and how the Tile was set. One folder per run of Tycho, in
``paths.tile_cards_dir()``; the newest ``RUNS`` are kept, and a run stops saving after
``LIMIT`` cards.

Like brain traces (``TYCHO_TRACE``), the lines keep what the player said and Tycho's
reply even with ``keep_transcripts`` off: never ask a player to turn it on without
telling them that. Nothing here leaves the PC. The card's text is untrusted (the brain
wrote it): it is only written as JSON data, never run or shown as markup.

The window's thread finds what's new (``drawn``, inside the display's lock); saving is
on a thread of its own, so a slow disk never holds up a frame.
"""

import json
import logging
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PIL import Image

from tycho import paths
from tycho.tile.board import Shown
from tycho.tile.cards import Card
from tycho.tile.draw import Look

log = logging.getLogger(__name__)

ENV = "TYCHO_TILE_RECORD"
FILE = "cards.jsonl"
RUNS = 10
"""Runs of Tycho whose cards are kept: older folders are deleted when a run starts."""
LIMIT = 500
"""Cards saved in one run, at most."""
WAITING = 20
"""Cards waiting to be saved: past that a card isn't saved, rather than hold the Tile."""
BACKDROP = (24, 26, 30, 255)
"""Behind the see-through Tile in the picture: about as dark as a cockpit."""


def enabled() -> bool:
    """Whether ``TYCHO_TILE_RECORD`` asks for a record ("1", "true", "yes", "on")."""
    return os.environ.get(ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def from_env() -> CardRecord | None:
    """A record in the logs folder when ``TYCHO_TILE_RECORD`` is on, else None."""
    return CardRecord(paths.tile_cards_dir()) if enabled() else None


class CardRecord:
    def __init__(
        self,
        folder: Path,
        *,
        now: Callable[[], datetime] = datetime.now,
        runs: int = RUNS,
        limit: int = LIMIT,
        background: bool = True,
    ) -> None:
        """``folder`` holds a folder per run. ``background=False`` saves at once, on the
        caller's thread: for tests."""
        self._folder = folder
        self._now = now
        self._runs = runs
        self._limit = limit
        self._background = background
        self._run: Path | None = None
        self._count = 0
        self._pending: list[tuple[Card, str, str, bool, datetime]] = []
        """Cards put up and not drawn yet: the card itself (matched by identity), what
        was asked, what was said, whether it was pinned, and when."""
        self._queue: queue.Queue[tuple[Path, Image.Image, dict[str, object]]] = queue.Queue(WAITING)
        self._thread: threading.Thread | None = None

    @property
    def run(self) -> Path | None:
        """This run's folder, once a card has been saved."""
        return self._run

    def shown(self, card: Card, *, asked: str = "", said: str = "", pinned: bool = False) -> None:
        """``card`` went up: it's saved when it's first drawn."""
        self._pending.append((card, asked, said, pinned, self._now()))

    def drawn(self, view: tuple[Shown, ...], image: Image.Image, look: Look) -> None:
        """A frame was drawn: save it for each card put up since the last one. A card
        that went before it was ever drawn is forgotten: nobody saw it."""
        if not self._pending:
            return
        for entry in self._pending:
            if any(s.card is entry[0] for s in view):
                self._save(entry, view, image, look)
        self._pending = []

    def close(self, timeout: float = 2.0) -> None:
        """Give what's waiting a moment to be saved, as Tycho stops."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)

    # Inside the display's lock.

    def _save(
        self,
        entry: tuple[Card, str, str, bool, datetime],
        view: tuple[Shown, ...],
        image: Image.Image,
        look: Look,
    ) -> None:
        if self._count >= self._limit:
            return
        card, asked, said, pinned, at = entry
        try:
            run = self._start()
        except OSError:
            log.debug("Couldn't make the Tile's record folder", exc_info=True)
            return
        self._count += 1
        name = f"{self._count:04d}-{card.kind}.png"
        line: dict[str, object] = {
            "at": at.isoformat(timespec="milliseconds"),
            "file": name,
            "kind": card.kind,
            "asked": asked,
            "said": said,
            "pinned": pinned,
            "card": asdict(card),
            "with": [  # the rest of the Tile in the picture, newest first
                {"kind": s.card.kind, "title": s.card.title, "strip": s.strip}
                for s in view
                if s.card is not card
            ],
            "size": [image.width, image.height],
            "look": {
                "scale": round(look.scale, 3),
                "see_through": look.see_through,
                "high_contrast": look.high_contrast,
                "corner": ("bottom" if look.bottom else "top")
                + ("-left" if look.left else "-right"),
                "accent": look.accent,
            },
        }
        job = (run / name, image.copy(), line)
        if not self._background:
            write(*job)
            return
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            log.debug("The Tile's record is behind: a card wasn't saved")
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._writer, name="tile-record", daemon=True)
            self._thread.start()

    def _start(self) -> Path:
        """This run's folder, made the first time, with older runs over ``runs`` gone."""
        if self._run is not None:
            return self._run
        self._folder.mkdir(parents=True, exist_ok=True)
        stamp = self._now().strftime("%Y-%m-%d_%H-%M-%S")
        run = self._folder / stamp
        n = 1
        while run.exists():
            n += 1
            run = self._folder / f"{stamp}_{n}"
        run.mkdir()
        older = sorted((p for p in self._folder.iterdir() if p.is_dir() and p != run), reverse=True)
        for old in older[max(self._runs - 1, 0) :]:
            shutil.rmtree(old, ignore_errors=True)
        self._run = run
        log.info("Saving the Tile's cards to %s", run)
        return run

    # On the record's own thread.

    def _writer(self) -> None:
        while True:
            job = self._queue.get()
            try:
                write(*job)
            except Exception:  # a full disk mustn't stop the Tile
                log.debug("Couldn't save a Tile card", exc_info=True)
            finally:
                self._queue.task_done()


def write(path: Path, image: Image.Image, line: dict[str, object]) -> None:
    """Save the picture on a dark backdrop, and its line beside it."""
    backdrop = Image.new("RGBA", image.size, BACKDROP)
    backdrop.alpha_composite(image.convert("RGBA"))
    backdrop.convert("RGB").save(path)
    with (path.parent / FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
