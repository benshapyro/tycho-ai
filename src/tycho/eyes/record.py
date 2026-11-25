"""A picture of the game window when a route fails on the map: a review tool (#661).

Off for players. ``TYCHO_ROUTE_RECORD=1`` turns it on (ADR 14's amendment of 25 November
2026, which the maintainer asked for after "no frame showed the map's search box" on his
PC could not be explained from the log). When the route flow stops on the map (it opened
the map and then failed), one more picture of the game window is taken and saved, with a
line in ``frames.jsonl`` beside it: when, the place, why the flow stopped, and the words
the last local check read, with their boxes, so the picture and what text recognition
made of it can be compared. Only the newest ``KEEP`` pictures are kept, in
``paths.route_frames_dir()``.

Nothing here leaves the PC, and nothing is saved with the setting off. The words are the
game's and untrusted (a marker can carry another player's name): they are only written as
JSON data, never run, said, logged or given to the brain. Saving runs on a thread of its
own, so it never holds up what Tycho says.
"""

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from tycho import paths
from tycho.eyes import CaptureError, Picture
from tycho.eyes.words import Reading

log = logging.getLogger(__name__)

ENV = "TYCHO_ROUTE_RECORD"
FILE = "frames.jsonl"
KEEP = 20
"""Pictures, and lines of the record, kept: older ones go as new ones are saved."""


class Camera(Protocol):
    """One picture of the game window (``eyes.windows.WindowsCamera``). Blocking."""

    def take(self) -> Picture: ...


def enabled() -> bool:
    """Whether ``TYCHO_ROUTE_RECORD`` asks for a record ("1", "true", "yes", "on")."""
    return os.environ.get(ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def from_env(camera: Camera) -> FrameRecord | None:
    """A record in the logs folder when ``TYCHO_ROUTE_RECORD`` is on, else None."""
    return FrameRecord(paths.route_frames_dir(), camera) if enabled() else None


class FrameRecord:
    def __init__(
        self,
        folder: Path,
        camera: Camera,
        *,
        now: Callable[[], datetime] = datetime.now,
        keep: int = KEEP,
        background: bool = True,
    ) -> None:
        """``background=False`` saves at once, on the caller's thread: for tests."""
        self._folder = folder
        self._camera = camera
        self._now = now
        self._keep = keep
        self._background = background
        self._lock = threading.Lock()

    def failed(self, place: str, why: str, reading: Reading | None) -> None:
        """The route to ``place`` stopped on the map for ``why``; ``reading`` is the last
        check's words, if any. Takes and saves a picture, off the caller's thread."""
        if not self._background:
            self._save(place, why, reading, self._now())
            return
        job = threading.Thread(
            target=self._save,
            args=(place, why, reading, self._now()),
            name="route-record",
            daemon=True,
        )
        job.start()

    def _save(self, place: str, why: str, reading: Reading | None, at: datetime) -> None:
        try:
            picture = self._camera.take()
        except CaptureError as e:
            picture = None
            log.info("No picture for the route record: %s", e)
        except Exception:  # never let a review tool stop Tycho
            log.debug("No picture for the route record", exc_info=True)
            picture = None
        try:
            with self._lock:
                self._write(place, why, reading, picture, at)
        except Exception:  # a full disk mustn't matter to the player
            log.debug("Couldn't save the route record", exc_info=True)

    def _write(
        self, place: str, why: str, reading: Reading | None, picture: Picture | None, at: datetime
    ) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        name = ""
        if picture is not None:
            stamp = at.strftime("%Y-%m-%d_%H-%M-%S")
            path = self._folder / f"{stamp}.jpg"
            n = 1
            while path.exists():
                n += 1
                path = self._folder / f"{stamp}_{n}.jpg"
            path.write_bytes(picture.data)
            name = path.name
            log.info("Saved a picture of the map for the route record: %s", path)
        line: dict[str, object] = {
            "at": at.isoformat(timespec="milliseconds"),
            "place": place,
            "why": why,
            "file": name,
            "size": [picture.width, picture.height] if picture is not None else None,
            "read": None
            if reading is None
            else {
                "size": [reading.width, reading.height],
                "words": [asdict(word) for word in reading.words],
            },
        }
        record = self._folder / FILE
        kept = record.read_text("utf-8").splitlines() if record.exists() else []
        kept = kept[len(kept) - self._keep + 1 :] if self._keep > 1 else []
        kept.append(json.dumps(line, ensure_ascii=False))
        record.write_text("\n".join(kept) + "\n", "utf-8")
        pictures = sorted(self._folder.glob("*.jpg"), reverse=True)
        for old in pictures[self._keep :]:
            old.unlink(missing_ok=True)
