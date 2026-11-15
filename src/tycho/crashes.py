"""Native crashes, recorded (#296). Nothing here is sent anywhere (ADR 4).

An access violation in a Windows call, ONNX Runtime, PortAudio or .NET under the app
window ends Tycho with no Python error and no log line. ``faulthandler`` writes what
every thread was doing to a file in the logs folder as the process dies; the next start
finds that file, keeps it under a dated name and tells the player where it is.

The file holds function names, file names and line numbers: no keys, and nothing the
player said. A clean stop removes it, whatever is in it: some Windows exceptions are
written there and then handled (.NET does this), and they aren't crashes.
"""

import atexit
import contextlib
import faulthandler
import logging
import time
from pathlib import Path
from typing import TextIO

from tycho import paths
from tycho.findings import Finding

log = logging.getLogger("tycho")

LIVE = "crash.txt"
"""Written while Tycho runs; still there at the next start only if Tycho didn't stop
cleanly."""
KEPT = "crash-*.txt"
KEEP = 5
"""How many past crashes stay in the logs folder."""
RECENT_SECONDS = 3 * 24 * 3600.0
"""How long ``tycho check`` and the window's status keep saying Tycho crashed."""
STOPPED = "Tycho stopped unexpectedly last time. What it was doing is in {file}."

_file: TextIO | None = None


def watch(folder: Path | None = None) -> Path | None:
    """Record a native crash of this run in ``folder`` (the logs folder). Returns the
    last run's record, under its dated name, if that run crashed."""
    folder = folder or paths.logs_dir()
    live = folder / LIVE
    stop()
    found = _keep(live)
    global _file
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _file = live.open("w", encoding="utf-8")
    except OSError as e:
        log.warning("Couldn't open %s, so a crash wouldn't be recorded: %s", live, e)
        return found
    faulthandler.enable(_file, all_threads=True)
    atexit.register(stop)
    return found


def stop() -> None:
    """Tycho is stopping cleanly: nothing crashed, so the file goes."""
    global _file
    if _file is None:
        return
    faulthandler.disable()
    path = Path(_file.name)
    _file.close()
    _file = None
    atexit.unregister(stop)
    with contextlib.suppress(OSError):
        path.unlink()


def _keep(live: Path) -> Path | None:
    """The last run's file, if it's there: kept under a dated name if anything was
    written to it, and removed if not (Tycho was ended from outside, or the PC lost
    power: nothing to read)."""
    try:
        if not live.exists():
            return None
        stat = live.stat()
        if stat.st_size == 0:
            live.unlink()
            return None
        when = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(stat.st_mtime))
        kept = live.with_name(f"crash-{when}.txt")
        live.replace(kept)
    except OSError as e:
        log.warning("Couldn't read the last run's crash file %s: %s", live, e)
        return None
    for old in past(live.parent)[KEEP:]:
        with contextlib.suppress(OSError):
            old.unlink()
    return kept


def past(folder: Path | None = None) -> list[Path]:
    """The crashes kept in the logs folder, the latest first."""
    folder = folder or paths.logs_dir()
    try:
        return sorted(folder.glob(KEPT), reverse=True)  # the names sort by date
    except OSError:
        return []


def finding(folder: Path | None = None, now: float | None = None) -> Finding | None:
    """What ``tycho check`` and the window's status say after a crash in the last few
    days; None if there wasn't one."""
    for record in past(folder)[:1]:
        try:
            at = record.stat().st_mtime
        except OSError:
            return None
        if (now if now is not None else time.time()) - at > RECENT_SECONDS:
            return None
        when = time.strftime("%d %B at %H:%M", time.localtime(at)).lstrip("0")
        return Finding(
            "warn",
            "Last crash",
            f"Tycho stopped unexpectedly on {when}. What it was doing is in {record}. "
            "If it keeps happening, attach that file to a report at "
            "github.com/benshapyro/tycho-ai/issues.",
        )
    return None
