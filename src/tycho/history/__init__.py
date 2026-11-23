"""Tycho's history of conversations (ADR 14's amendment of 23 November 2026, #484).

Only with ``keep_history`` on, which is off until the player says yes. One history per
play session, from start to quit: what the player said, what Tycho said, and the game
actions it pressed (their names, never keys), each with its time. No audio, no pictures.
It's kept on this PC only, in Tycho's data folder (``paths.history_dir``), one JSON line
per exchange, and deleted after ``KEEP_DAYS``. Separate from ``keep_transcripts``, the
debugging log.

What's kept is the player's and the brain's words: untrusted text. A search returns only
short matching parts (``History.search``), and the brain's ``history`` look-up
(``history.tool``) hands them over as text to answer from, never as permission to press.
"""

import datetime
import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from tycho import paths
from tycho.runtime.messages import Result
from tycho.runtime.recent import exchange
from tycho.text import normalize
from tycho.tile.cards import clean

if TYPE_CHECKING:
    from tycho.spoken_settings import Live

log = logging.getLogger(__name__)

KEEP_DAYS = 30
"""How long a session's history is kept, from the day of its last exchange."""
SAID_CHARS = 300
"""The most of what the player said that's kept."""
TYCHO_CHARS = 1200
"""The most of what Tycho said that's kept: a route or a how-to, whole."""
HIT_CHARS = 300
"""The most of one line a search hands back: "only the matching parts"."""
MOST_HITS = 5
"""The most exchanges one search hands back."""

_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
"""A session's id: when it started, ``2026-11-22_21-04-09``. Its file is ``<id>.jsonl``."""
_COMMON = frozenset(
    {"the", "a", "an", "i", "my", "me", "you", "your", "we", "that", "this", "it", "is", "was"}
    | {"are", "were", "to", "of", "in", "on", "at", "for", "and", "or", "what", "did", "do"}
    | {"tell", "told", "say", "said", "about", "again", "yesterday", "today", "earlier", "last"}
    | {"time", "when", "where", "which", "how", "us", "give", "gave", "remind", "one"}
    | {"talk", "talked", "chat", "chatted", "speak", "spoke", "discuss", "discussed", "we"}
)
"""Words too common to find an exchange by."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One exchange: when it ended, what the player said, Tycho's words, what it pressed."""

    at: datetime.datetime
    said: str
    tycho: str = ""
    did: tuple[str, ...] = ()

    def line(self) -> str:
        return json.dumps(
            {"at": self.at.isoformat(timespec="seconds"), "said": self.said, "tycho": self.tycho}
            | ({"did": list(self.did)} if self.did else {}),
            ensure_ascii=False,
        )

    @classmethod
    def read(cls, line: str) -> Entry | None:
        """An entry from a file line, or None for one that isn't (a hand edit, a cut write)."""
        try:
            data: object = json.loads(line)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        kept = cast(dict[str, object], data)
        try:
            at = datetime.datetime.fromisoformat(str(kept.get("at")))
        except ValueError:
            return None
        if at.tzinfo is None:  # a hand edit without a timezone: this PC's (#587)
            at = at.astimezone()
        did = kept.get("did", [])
        names = (
            tuple(clean(d, 80) for d in cast(list[object], did)) if isinstance(did, list) else ()
        )
        said, tycho = clean(kept.get("said"), SAID_CHARS), clean(kept.get("tycho"), TYCHO_CHARS)
        return cls(at, said, tycho, tuple(n for n in names if n)) if said or tycho else None


@dataclass(frozen=True, slots=True)
class Played:
    """One play session, as the History page lists it."""

    id: str
    started: datetime.datetime
    exchanges: int


@dataclass(frozen=True, slots=True)
class Found:
    """An exchange a search found, and the session it's in."""

    session: str
    entry: Entry


class HistoryStore(Protocol):
    """Where the sessions are kept (a folder of files; a fake in tests)."""

    def sessions(self) -> list[Played]:
        """Every session kept, newest first."""
        ...

    def entries(self, session: str) -> list[Entry]: ...

    def append(self, session: str, entry: Entry) -> None: ...

    def delete(self, session: str) -> bool:
        """Delete one session. Returns whether it was there."""
        ...


def session_id(started: datetime.datetime) -> str:
    return f"{started:%Y-%m-%d_%H-%M-%S}"


def started_at(session: str) -> datetime.datetime | None:
    """When a session started, from its id; None for a name that isn't one."""
    if not _NAME.match(session):
        return None
    try:
        return datetime.datetime.strptime(session, "%Y-%m-%d_%H-%M-%S").astimezone()
    except ValueError:
        return None


class HistoryFolder:
    """Sessions as JSON-lines files in a folder, one per session. Satisfies ``HistoryStore``."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _file(self, session: str) -> Path:
        if started_at(session) is None:  # never a path the page or a file name chose
            raise ValueError(f"{session!r} isn't a session")
        return self.folder / f"{session}.jsonl"

    def sessions(self) -> list[Played]:
        if not self.folder.is_dir():
            return []
        found: list[Played] = []
        for path in self.folder.glob("*.jsonl"):
            if (started := started_at(path.stem)) is not None:
                found.append(Played(path.stem, started, len(self.entries(path.stem))))
        return sorted(found, key=lambda p: p.started, reverse=True)

    def entries(self, session: str) -> list[Entry]:
        try:
            text = self._file(session).read_text("utf-8")
        except OSError, UnicodeDecodeError, ValueError:
            return []
        return [e for line in text.splitlines() if line.strip() and (e := Entry.read(line))]

    def append(self, session: str, entry: Entry) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with self._file(session).open("a", encoding="utf-8") as f:
            f.write(entry.line() + "\n")

    def delete(self, session: str) -> bool:
        try:
            self._file(session).unlink()
        except FileNotFoundError, ValueError:
            return False
        return True


class History:
    """This play session's history and every kept one, and whether history is on (the
    ``keep_history`` setting). ``record`` is the session's ``on_result``."""

    def __init__(
        self,
        store: HistoryStore,
        *,
        on: bool = False,
        now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now().astimezone(),
    ) -> None:
        self.store = store
        self.on = on
        self.now = now
        self.session = ""
        """This play session's id, once something was kept; empty before."""
        self._writer = ThreadPoolExecutor(1, thread_name_prefix="tycho-history")
        self.lock = threading.Lock()

    def set_on(self, value: object) -> None:
        """``keep_history``, changed in the app or (only off) by voice. Off keeps what's
        there: deleting is the player's own choice, on the History page."""
        self.on = value is True

    def record(self, result: Result) -> None:
        """Keep one exchange, if history is on and something was said. Written on a
        thread of its own: the talking loop never waits for the disk."""
        if not self.on or result.tried or result.would_press:  # setup's Try it: not play
            return
        at = self.now()
        shown = exchange(result, at.timestamp())
        if shown is None:
            return
        entry = Entry(
            at,
            clean(shown.said, SAID_CHARS),
            clean(shown.did, TYCHO_CHARS),
            tuple(result.pressed),
        )
        if not self.session:
            self.session = session_id(entry.at)
        self._writer.submit(self._append, self.session, entry)

    def _append(self, session: str, entry: Entry) -> None:
        try:
            with self.lock:
                self.store.append(session, entry)
        except OSError as e:  # a full disk mustn't stop the loop
            log.warning("Couldn't keep the history: %s", e)

    def close(self) -> None:
        """Finish writing what was said. Tycho is quitting."""
        self._writer.shutdown(wait=True)

    def prune(self) -> int:
        """Delete every session whose last exchange is more than ``KEEP_DAYS`` days old,
        whether history is on or not: one Tycho ran through for days keeps its recent
        exchanges (#587). Returns how many went."""
        oldest = self.now().date() - datetime.timedelta(days=KEEP_DAYS)
        gone = 0
        with self.lock:
            for played in self.store.sessions():
                if played.started.date() >= oldest:
                    continue
                entries = self.store.entries(played.id)
                last = max((e.at for e in entries), default=played.started)
                if last.astimezone().date() < oldest and self.store.delete(played.id):
                    gone += 1
        if gone:
            log.info("History: deleted %d session(s) over %d days old", gone, KEEP_DAYS)
        return gone

    def delete(self, session: str) -> bool:
        with self.lock:
            if session == self.session:
                self.session = ""  # what's said next starts a new session (#587)
            return self.store.delete(session)

    def delete_all(self) -> int:
        with self.lock:
            self.session = ""
            return sum(self.store.delete(p.id) for p in self.store.sessions())

    def search(
        self, words: str, day: datetime.date | None = None, most: int = MOST_HITS
    ) -> list[Found]:
        """The exchanges that best match ``words`` (on ``day`` only, if given), at most
        ``most``, oldest first. With no words to go by, the last exchanges."""
        wanted = _stems(normalize(words)) - _COMMON
        scored: list[tuple[int, datetime.datetime, Found]] = []
        for played in self.store.sessions():
            if day is not None and played.started.date() > day:
                continue  # started after that day; one that started before may run into it
            for entry in self.store.entries(played.id):
                if day is not None and entry.at.astimezone().date() != day:
                    continue
                score = _matches(wanted, _stems(normalize(f"{entry.said} {entry.tycho}")))
                if score or not wanted:
                    scored.append((score, entry.at, Found(played.id, entry)))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return sorted((s[2] for s in scored[:most]), key=lambda f: f.entry.at)


def _stems(words: list[str]) -> set[str]:
    """Words without a plural's s, so "routes" finds "route"."""
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


def _matches(wanted: set[str], words: set[str]) -> int:
    """How many words wanted are in ``words``, or start one ("land" finds "landing")."""
    return sum(
        any(
            w == x or (min(len(w), len(x)) >= 4 and (x.startswith(w) or w.startswith(x)))
            for x in words
        )
        for w in wanted
    )


def kept(on: bool) -> History:
    """The player's history in Tycho's data folder, with what's over ``KEEP_DAYS`` old
    deleted now."""
    history = History(HistoryFolder(paths.history_dir()), on=on)
    try:
        history.prune()
    except OSError as e:
        log.warning("Couldn't delete old history: %s", e)
    return history


def follow(live: Live, history: History) -> None:
    """Keep ``history.on`` in step with ``keep_history`` changed while Tycho runs, beside
    anything else that follows it (the recording and the brain's look-up)."""
    before = live.get("keep_history")

    def turned(value: object) -> None:
        history.set_on(value)
        if before is not None:
            before(value)

    live["keep_history"] = turned
