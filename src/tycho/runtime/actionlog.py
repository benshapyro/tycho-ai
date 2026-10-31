"""The action log: one JSON line per request that matched a command (ADR 13).

It records what Tycho did and why, for the player to review: the commands, how
the request ended, the game actions pressed, and whether the player confirmed.
What was said is only included if the player keeps transcripts. Lines pass
through the same redaction as every other log.
"""

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from tycho import paths
from tycho.logs import redact

ACTION_LOG_FILE = "actions.jsonl"
_MAX_BYTES = 2_000_000
_BACKUPS = 3


class _Line(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(record.getMessage())


class FileActionLog:
    """Satisfies ``runtime.ActionLog``. Appends to a rotating file in the logs folder."""

    def __init__(self, folder: Path | None = None) -> None:
        folder = folder or paths.logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / ACTION_LOG_FILE
        self._handler = logging.handlers.RotatingFileHandler(
            self.path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        self._handler.setFormatter(_Line())
        # Its own logger, not under "tycho", so actions don't also fill the diagnostic log.
        self._log = logging.getLogger(f"tycho-actions.{id(self)}")
        self._log.propagate = False
        self._log.setLevel(logging.INFO)
        self._log.addHandler(self._handler)

    def record(self, entry: dict[str, object]) -> None:
        line = {"time": datetime.now().astimezone().isoformat(timespec="milliseconds"), **entry}
        self._log.info(json.dumps(line, ensure_ascii=False))

    def close(self) -> None:
        self._log.removeHandler(self._handler)
        self._handler.close()
