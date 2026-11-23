"""The blueprints the player owns (ADR 8's third amendment, 23 November 2026, #598).

A crafting blueprint, once a mission gives it, is the player's for good. Game.log
records each one with the HUD's "Received Blueprint: <item>" line (``loglines``), so
Tycho keeps a list: from that line in the current Game.log and, once, in the game's
saved logs (``logbackups``), and from the player's own words ("I've got the Killshot
Rifle blueprint"). Nothing else adds to it.

The list is a plain file the player can read and edit, ``blueprints.toml`` beside that
game's notes (``paths.blueprints_file``). Each entry is an item's name that passes
``loglines.ITEM_NAME``, checked when saved and again when read: never free text,
never an instruction. It's used only when asked (the ``owned_blueprints`` look-up,
and the blueprint look-up's "you own it"), never sent with every request, and
nothing is added or used while ``memory`` is off.
"""

# The parsed file's values are loosely typed: each is checked as it's read.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import datetime
import logging
import threading
import tomllib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from tycho.games.star_citizen import loglines
from tycho.settings_toml import write_atomic

log = logging.getLogger(__name__)

MOST = 1000
"""The most blueprints kept: far more than the game gives."""
_MARKER = b"Received Blueprint: "
_READ = 1 << 20
NEW_FILE = (
    "# The blueprints you own, as far as Tycho knows (ADR 8). Tycho adds one when the\n"
    "# game's log says you received it, or when you tell it. Delete a line here, say\n"
    '# "forget the ... blueprint", or use the Memory page in Tycho\'s window.\n'
)
SOURCES = ("log", "you")


@dataclass(frozen=True, slots=True)
class Owned:
    name: str
    got: datetime.date | None
    source: str
    """ "log": the game's log said so; "you": the player did."""


def item(name: object) -> str:
    """``name`` as a list entry, or "" if it isn't an item's name (checked on save and read)."""
    if not isinstance(name, str):
        return ""
    name = loglines.clean(name)
    return name if loglines.ITEM_NAME.fullmatch(name) else ""


def received(
    path: Path, names: Callable[[str], str] | None
) -> Iterator[tuple[str, datetime.date | None]]:
    """Every blueprint ``path`` (a Game.log) says the player received, with its day.
    Only the one line counts; a key the game's text can't name is skipped."""
    tail = b""
    try:
        with path.open("rb") as file:
            while chunk := file.read(_READ):
                data = tail + chunk
                lines = data.split(b"\n")
                tail = lines.pop()
                if _MARKER in data:
                    yield from _blueprints(lines, names)
            yield from _blueprints([tail], names)
    except OSError as e:
        log.warning("Couldn't read %s for blueprints: %s", path.name, e)


def _blueprints(
    lines: Iterable[bytes], names: Callable[[str], str] | None
) -> Iterator[tuple[str, datetime.date | None]]:
    for raw in lines:
        if _MARKER not in raw or not (line := loglines.read(raw)):
            continue
        if line.key and names is not None:
            line = loglines.named_blueprint(line, names(line.key))
        if line.kind == loglines.BLUEPRINT:
            at = loglines.when(raw)
            yield line.name, at.date() if at else None


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """One lock per list file, however many ``OwnedBlueprints`` read it: the Memory
    page makes its own, and a log read beside its delete wrote the delete away (#608)."""
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(path.absolute(), threading.Lock())


class OwnedBlueprints:
    """The list, read fresh from its file each time: the player may edit it."""

    def __init__(
        self,
        path: Path,
        folder: Callable[[], Path | None],
        names: Callable[[str], str] | None = None,
        *,
        on: bool = True,
        today: Callable[[], datetime.date] = datetime.date.today,
    ) -> None:
        """``folder``: the game's channel folder (where Game.log is), if found.
        ``names``: the game's text for an item's key (``localization.lazy_names``)."""
        self.path = path
        self._folder = folder
        self._names = names
        self.on = on
        self._today = today
        self.lock = _lock_for(path)
        """Held while the list changes: the look-up and the Memory page share the file."""
        self.problem = ""

    def set_on(self, value: object) -> None:
        """The ``memory`` setting changed."""
        self.on = value is True

    # --- Reading and writing the file ----------------------------------------------

    def _read(self) -> tuple[list[Owned], float]:
        owned, scanned, _ = self._read_all()
        return owned, scanned

    def _read_all(self) -> tuple[list[Owned], float, list[str]]:
        """The list, the newest saved log read, and what the player took off the list
        (the log never adds those back: only the player can)."""
        if not self.path.exists():
            self.problem = ""
            return [], 0.0, []
        try:
            data = tomllib.loads(self.path.read_text("utf-8-sig"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            self.problem = f"Couldn't read {self.path.name}: {e}"
            log.warning("%s", self.problem)
            raise OSError(self.problem) from e
        self.problem = ""
        out: list[Owned] = []
        seen: set[str] = set()
        entries = data.get("blueprint", [])
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            name = item(entry.get("name"))
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            got = entry.get("got")
            source = entry.get("from")
            out.append(
                Owned(
                    name,
                    got if isinstance(got, datetime.date) else None,
                    source if source in SOURCES else "you",
                )
            )
        scanned = data.get("scanned_logs", 0.0)
        taken = data.get("taken_off", [])
        taken_off = [n for t in (taken if isinstance(taken, list) else []) if (n := item(t))]
        when = float(scanned) if isinstance(scanned, int | float) else 0.0
        return out[:MOST], when, taken_off[:MOST]

    def _write(self, owned: list[Owned], scanned: float, taken_off: list[str]) -> None:
        lines = [NEW_FILE, f"scanned_logs = {scanned!r}  # the newest saved log already read\n"]
        names = ", ".join(_quoted(n) for n in taken_off)
        lines.append(f"taken_off = [{names}]  # the log never adds these back\n")
        for entry in owned:
            lines.append("\n[[blueprint]]\n")
            lines.append(f"name = {_quoted(entry.name)}\n")
            if entry.got is not None:
                lines.append(f"got = {entry.got.isoformat()}\n")
            lines.append(f'from = "{entry.source}"\n')
        text = "".join(lines)
        tomllib.loads(text)  # never write a file Tycho couldn't read back
        write_atomic(self.path, text)

    # --- What the look-ups and the Memory page use ----------------------------------

    def listed(self) -> list[Owned]:
        """The list as it stands, without reading the logs. Empty while memory is off."""
        if not self.on:
            return []
        try:
            return self._read()[0]
        except OSError:
            return []

    def refresh(self) -> list[Owned]:
        """Read what the logs say that the list doesn't have yet, then the list. The saved
        logs are read once each (newer than the last one read); the current Game.log
        each time. Blocking: call it off the event loop."""
        if not self.on:
            return []
        folder = self._folder()
        with self.lock:
            try:
                owned, scanned, taken_off = self._read_all()
            except OSError:
                return []
            if folder is None:
                return owned
            found, newest = self._from_logs(folder, scanned)
            have = {o.name.casefold() for o in owned} | {n.casefold() for n in taken_off}
            added = 0
            for name, got in found:
                if name.casefold() not in have and len(owned) < MOST:
                    owned.append(Owned(name, got, "log"))
                    have.add(name.casefold())
                    added += 1
            if added or newest > scanned:
                try:
                    self._write(owned, max(newest, scanned), taken_off)
                except OSError as e:
                    log.warning("Couldn't save the blueprints list: %s", e)
            if added:
                log.info("Blueprints: %d added from the game's logs", added)
            return owned

    def _from_logs(
        self, folder: Path, scanned: float
    ) -> tuple[list[tuple[str, datetime.date | None]], float]:
        found: list[tuple[str, datetime.date | None]] = []
        newest = scanned
        saved = folder / "logbackups"
        try:
            backups = sorted(saved.glob("*.log")) if saved.is_dir() else []
        except OSError:
            backups = []
        for path in backups:
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified <= scanned:
                continue
            found.extend(received(path, self._names))
            newest = max(newest, modified)
        found.extend(received(folder / "Game.log", self._names))
        return found, newest

    def add(self, name: str) -> str:
        """The player said they own ``name`` (an item's name): "added", "already_listed",
        "not_an_item_name" (it fails the check), "full" or "off"."""
        if not self.on:
            return "off"
        if not (name := item(name)):
            return "not_an_item_name"
        with self.lock:
            owned, scanned, taken_off = self._read_all()
            if any(o.name.casefold() == name.casefold() for o in owned):
                return "already_listed"
            if len(owned) >= MOST:
                return "full"
            owned.append(Owned(name, self._today(), "you"))
            kept_off = [n for n in taken_off if n.casefold() != name.casefold()]
            self._write(owned, scanned, kept_off)
        log.info("Blueprints: one added by the player")
        return "added"

    def remove(self, name: str) -> bool:
        """Take ``name`` off the list (exactly as listed, any case)."""
        with self.lock:
            owned, scanned, taken_off = self._read_all()
            gone = [o for o in owned if o.name.casefold() == name.casefold()]
            if not gone:
                return False
            kept = [o for o in owned if o not in gone]
            self._write(kept, scanned, [*taken_off, gone[0].name][-MOST:])
        log.info("Blueprints: one removed")
        return True


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mark(body: dict[str, object], owned: Iterable[Owned]) -> dict[str, object]:
    """The blueprint look-up's answer with what the player owns marked: "you_own_it"
    for one blueprint, "you_own" for the ones a mission or giver lists."""
    have = {o.name.casefold() for o in owned}
    if not have:
        return body
    if isinstance(name := body.get("blueprint"), str):
        if name.casefold() in have:
            body["you_own_it"] = True
        return body
    listed: list[str] = []
    _names_in(body, listed)
    if mine := sorted({n for n in listed if n.casefold() in have}):
        body["you_own"] = mine
    return body


def _names_in(value: object, out: list[str]) -> None:
    """Every blueprint name in a mission answer: its "blueprints" lists, however deep."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "blueprints" and isinstance(inner, list):
                out.extend(n for n in inner if isinstance(n, str))
            else:
                _names_in(inner, out)
    elif isinstance(value, list):
        for inner in value:
            _names_in(inner, out)
