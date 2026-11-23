"""Native crashes, recorded (#296). Nothing here is sent anywhere (ADR 4).

An access violation in a Windows call, ONNX Runtime, PortAudio or .NET under the app
window ends Tycho with no Python error and no log line. ``faulthandler`` writes what
every thread was doing to a file in the logs folder as the process dies; the next start
finds that file, keeps it under a dated name and tells the player where it is.

The file holds function names, file names and line numbers: no keys, and nothing the
player said. A clean stop removes it, whatever is in it: some Windows exceptions are
written there and then handled (.NET does this), and they aren't crashes. When Tycho
was ended from outside (Task Manager) after one of those, the next start finds only
handled exceptions in the file, and doesn't call that a crash either (#474).
"""

import atexit
import contextlib
import faulthandler
import logging
import re
import threading
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

NOTE = (
    "Tycho is running. A native crash is written below. So are some Windows exceptions "
    "that Windows or .NET then handles, such as code 0x8001010d: Tycho goes on after "
    "those, and this file is removed when Tycho stops.\n\n"
)
"""The live file's first lines (#582): a "Windows fatal exception" written here while
Tycho goes on isn't a crash, and anyone reading the file mid-run should know that."""

HANDLED = frozenset({"code 0x8001010d"})
"""Windows exceptions ``faulthandler`` writes that Windows or .NET then handles (#474).
0x8001010d: a COM call refused during an input-synchronous call, which the app
window's message loop (WebView2 under WinForms) recovers from. Python itself already
leaves out C++ and .NET's own exceptions."""
_HEADER = re.compile(r"^(?:Windows fatal exception: (.+)|Fatal Python error: .*)$", re.M)

_file: TextIO | None = None
_lock = threading.RLock()
"""``stop`` runs on Windows' threads too: the console's close and ``WM_ENDSESSION``
(#440)."""


def watch(folder: Path | None = None) -> Path | None:
    """Record a native crash of this run in ``folder`` (the logs folder). Returns the
    last run's record, under its dated name, if that run crashed."""
    folder = folder or paths.logs_dir()
    live = folder / LIVE
    global _file
    with _lock:
        stop()
        found = _keep(live)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            _file = live.open("w", encoding="utf-8")
            _file.write(NOTE)
            _file.flush()  # faulthandler writes after it, straight to the file
        except OSError as e:
            log.warning("Couldn't open %s, so a crash wouldn't be recorded: %s", live, e)
            return found
        faulthandler.enable(_file, all_threads=True)
        atexit.register(stop)
        return found


def stop() -> None:
    """Tycho is stopping cleanly: nothing crashed, so the file goes. Safe from any
    thread, and more than once."""
    global _file
    with _lock:
        if _file is None:
            return
        faulthandler.disable()
        path = Path(_file.name)
        _file.close()
        _file = None
        atexit.unregister(stop)
    with contextlib.suppress(OSError):
        path.unlink()


def only_handled(record: str) -> bool:
    """Whether a record holds nothing but exceptions that get handled (``HANDLED``):
    Tycho went on after each, so it didn't crash there (#474)."""
    headers = list(_HEADER.finditer(record))
    return bool(headers) and all(header.group(1) in HANDLED for header in headers)


def _keep(live: Path) -> Path | None:
    """The last run's file, if it's there: kept under a dated name if anything was
    written to it after ``NOTE``, and removed if not (Tycho was ended from outside, or
    the PC lost power: nothing to read)."""
    try:
        if not live.exists():
            return None
        stat = live.stat()
        record = live.read_text(encoding="utf-8", errors="replace").removeprefix(NOTE)
        if not record.strip():
            live.unlink()
            return None
        if only_handled(record):
            log.info("The last run's %s held only handled Windows exceptions", live.name)
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
