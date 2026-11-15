"""Crash reports, sent only if the player says yes (ADR 4's amendment, #199).

A crash is an exception nothing caught. When the player has said yes, Tycho writes a
small report to ``paths.crash_reports_dir()`` and sends it to Tycho's Sentry project
the next time it starts. Nothing is sent from a run that started with reports off,
and saying no deletes any report still waiting.

A report is built from an allowlist, not cut down from everything: the error's type,
where it happened in Tycho's own code (module, function, line), Tycho's version, the
Windows version, the game pack's id and the brain provider. The exception's message,
local variables, log lines and file paths are never read into it. ``scrub`` then
checks every field against its pattern and against values that must never leave the
PC (stored keys, the player's names, the Windows user name); a report that fails is
dropped, never sent as it is. No Sentry SDK: Tycho writes the few fields itself, so
nothing else can be added on the way out.
"""

import json
import logging
import os
import platform
import re
import sys
import threading
import traceback
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, cast
from urllib.parse import urlsplit

import httpx2

from tycho import __version__, logs, paths

log = logging.getLogger(__name__)

DSN = ""
"""Tycho's Sentry project. Empty until the maintainer creates it, and while it's empty
nothing is written or sent, whatever the setting says."""
MAX_WAITING = 5
"""Reports kept to send at the next start; a crash loop doesn't fill the disk."""
MAX_FRAMES = 40
TIMEOUT_SECONDS = 5.0

_TYCHO = Path(__file__).resolve().parent
_FIELDS: Mapping[str, re.Pattern[str]] = {
    "kind": re.compile(r"python"),
    "error": re.compile(r"[A-Za-z_][\w.]{0,120}"),
    "tycho": re.compile(r"[\w.+-]{1,40}"),
    "windows": re.compile(r"[\w .()-]{0,60}"),
    "pack": re.compile(r"[a-z0-9-]{1,40}"),
    "provider": re.compile(r"[a-z0-9-]{1,40}"),
    "time": re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"),
}
_WINDOWS_ONLY = re.compile(r"[^\w .()-]")
_WORD = re.compile(r"[a-z0-9]+")
_MODULE = re.compile(r"[A-Za-z_][\w.]{0,120}")
_FUNCTION = re.compile(r"[<>\w.]{1,120}")


@dataclass(frozen=True)
class Context:
    """What a report says about this Tycho, besides the crash."""

    pack: str
    provider: str


def build(error: BaseException, context: Context) -> dict[str, Any]:
    """A report of ``error``, from the allowlist only: never its message or locals."""
    return {
        "kind": "python",
        "error": _error_type(type(error)),
        "frames": list(_frames(error.__traceback__))[-MAX_FRAMES:],
        "tycho": __version__,
        "windows": _WINDOWS_ONLY.sub("", f"{platform.release()} {platform.version()}")[:60],
        "pack": context.pack,
        "provider": context.provider,
        "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _error_type(kind: type[BaseException]) -> str:
    module = kind.__module__
    return kind.__qualname__ if module == "builtins" else f"{module}.{kind.__qualname__}"


def _frames(tb: TracebackType | None) -> Iterable[dict[str, object]]:
    """Tycho's own frames by module, function and line; anyone else's by package only,
    so a path or a script name outside Tycho never goes in."""
    for frame, line in traceback.walk_tb(tb):
        module = str(frame.f_globals.get("__name__", ""))
        if module.startswith("tycho") and _is_tycho(frame.f_globals.get("__file__")):
            yield {"module": module, "function": frame.f_code.co_name, "line": line}
        else:
            package = module.split(".")[0]
            yield {"module": package if _MODULE.fullmatch(package) else "other"}


def _is_tycho(filename: object) -> bool:
    """Whether a frame's module file is inside Tycho. The module's ``__file__``, not the
    code's ``co_filename``, which PyInstaller makes relative in the installed build."""
    if not isinstance(filename, str):
        return False
    try:
        return Path(filename).resolve().is_relative_to(_TYCHO)
    except OSError, ValueError:
        return False


def scrub(report: Mapping[str, Any], never: Iterable[str]) -> dict[str, Any] | None:
    """The report if every field is what the allowlist expects and none of ``never``
    is in it; otherwise None, and it isn't sent."""
    try:
        clean = _checked(report)
    except KeyError, TypeError, ValueError:
        return None
    text = json.dumps(clean, ensure_ascii=False).lower()
    if logs.redact(text) != text:
        return None
    words = set(_WORD.findall(text))
    for value in never:
        # Whole words, so the user name "ben" doesn't drop a report naming "bench".
        if any(len(w) >= 3 and w in words for w in _WORD.findall(value.lower())):
            return None
    return clean


def _checked(report: Mapping[str, Any]) -> dict[str, Any]:
    if set(report) != {*_FIELDS, "frames"}:
        raise KeyError("unexpected fields")
    clean: dict[str, Any] = {}
    for name, pattern in _FIELDS.items():
        value = report[name]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(name)
        clean[name] = value
    frames: object = report["frames"]
    if not isinstance(frames, list) or len(cast(list[object], frames)) > MAX_FRAMES:
        raise ValueError("frames")
    clean["frames"] = [_checked_frame(f) for f in cast(list[object], frames)]
    return clean


def _checked_frame(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise TypeError("frame")
    frame = cast(dict[str, object], raw)
    module = frame.get("module")
    if not isinstance(module, str) or not _MODULE.fullmatch(module):
        raise ValueError("module")
    if set(frame) == {"module"}:
        return {"module": module}
    function, line = frame.get("function"), frame.get("line")
    if set(frame) != {"module", "function", "line"} or not module.startswith("tycho"):
        raise KeyError("frame fields")
    if not isinstance(function, str) or not _FUNCTION.fullmatch(function):
        raise ValueError("function")
    if not isinstance(line, int) or isinstance(line, bool) or not 0 <= line < 100_000:
        raise ValueError("line")
    return {"module": module, "function": function, "line": line}


def never_sent(call_me: Iterable[str] = ()) -> list[str]:
    """Values that must never be in a report: the Windows user name and what Tycho calls
    the player. Stored keys are caught by ``logs.redact``; paths can't pass the patterns."""
    values = [os.environ.get("USERNAME", ""), os.environ.get("USER", "")]
    return [v for v in (*values, *call_me) if v]


def keep(report: dict[str, Any], folder: Path | None = None) -> Path | None:
    """Write a scrubbed report to send at the next start. At most ``MAX_WAITING``."""
    folder = folder or paths.crash_reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if len(list(folder.glob("*.json"))) >= MAX_WAITING:
        return None
    path = folder / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def forget(folder: Path | None = None) -> int:
    """Delete every report waiting to be sent: the player said no. Returns how many."""
    folder = folder or paths.crash_reports_dir()
    gone = 0
    for path in folder.glob("*.json") if folder.is_dir() else ():
        path.unlink(missing_ok=True)
        gone += 1
    return gone


type Post = Callable[[str, Mapping[str, str], bytes], int]
"""Sends a body to a URL with headers; returns the HTTP status."""


def send_waiting(
    dsn: str,
    allowed: Callable[[], bool],
    never: Iterable[str],
    *,
    post: Post | None = None,
    folder: Path | None = None,
) -> int:
    """Send the reports waiting from earlier runs, checking the setting before each.
    A report that fails ``scrub`` is deleted unsent. Returns how many were sent."""
    folder = folder or paths.crash_reports_dir()
    never = list(never)
    post = post or _post
    sent = 0
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        if not allowed():
            forget(folder)
            return sent
        try:
            report = scrub(json.loads(path.read_text(encoding="utf-8")), never)
        except OSError, ValueError:
            report = None
        if report is None:
            path.unlink(missing_ok=True)
            continue
        url, headers, body = envelope(report, dsn)
        try:
            status = post(url, headers, body)
        except httpx2.HTTPError:
            return sent  # offline: try again at the next start
        if status < 500:  # sent, or refused for good
            path.unlink(missing_ok=True)
            sent += status < 300
    return sent


def envelope(report: Mapping[str, Any], dsn: str) -> tuple[str, dict[str, str], bytes]:
    """Sentry's envelope for one report: the URL, the headers and the body."""
    parts = urlsplit(dsn)
    key, project = parts.username or "", parts.path.strip("/")
    url = f"{parts.scheme}://{parts.hostname}{f':{parts.port}' if parts.port else ''}"
    url += f"/api/{project}/envelope/"
    headers = {
        "Content-Type": "application/x-sentry-envelope",
        "X-Sentry-Auth": f"Sentry sentry_version=7, sentry_key={key}, "
        f"sentry_client=tycho/{report['tycho']}",
    }
    event_id = uuid.uuid4().hex  # one per report, never kept: not an ID for the PC
    frames = [
        {
            "module": f["module"],
            "function": f.get("function", "?"),
            "lineno": f.get("line", 0),
            "in_app": "line" in f,
        }
        for f in report["frames"]
    ]
    event = {
        "event_id": event_id,
        "timestamp": report["time"],
        "platform": "python",
        "level": "fatal",
        "release": f"tycho@{report['tycho']}",
        "exception": {"values": [{"type": report["error"], "stacktrace": {"frames": frames}}]},
        "contexts": {"os": {"name": "Windows", "version": report["windows"].strip()}},
        "tags": {"pack": report["pack"], "provider": report["provider"]},
        "user": {"ip_address": None},
    }
    lines = [
        json.dumps({"event_id": event_id}),
        json.dumps({"type": "event"}),
        json.dumps(event),
    ]
    return url, headers, ("\n".join(lines) + "\n").encode()


def _post(url: str, headers: Mapping[str, str], body: bytes) -> int:
    with httpx2.Client(timeout=TIMEOUT_SECONDS) as client:
        return client.post(url, headers=dict(headers), content=body).status_code


def install(
    allowed: Callable[[], bool], context: Context, never: Iterable[str], folder: Path | None = None
) -> None:
    """Keep a report of each crash from now on, while ``allowed()`` says yes. The usual
    handling (the traceback in the log) still runs first."""
    never = list(never)

    def kept(error: BaseException) -> None:
        try:
            if allowed() and (report := scrub(build(error, context), never)) is not None:
                keep(report, folder)
        except Exception:
            log.debug("Couldn't keep a crash report", exc_info=True)

    previous_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def hook(kind: type[BaseException], error: BaseException, tb: TracebackType | None) -> None:
        previous_hook(kind, error, tb)
        if not isinstance(error, KeyboardInterrupt | SystemExit):
            kept(error)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        previous_thread_hook(args)
        if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
            kept(args.exc_value)

    global _keep_crash
    _keep_crash = kept
    sys.excepthook = hook
    threading.excepthook = thread_hook


_keep_crash: Callable[[BaseException], None] | None = None


def keep_crash(error: BaseException) -> None:
    """Keep a report of a crash that something caught on its way out, such as the Start
    menu's Tycho, which shows it in a message box (#429). Does nothing unless reports
    were on when this Tycho started."""
    if _keep_crash is not None and not isinstance(error, KeyboardInterrupt | SystemExit):
        _keep_crash(error)


def start(
    setting: bool | None,
    allowed: Callable[[], bool],
    context: Context,
    call_me: Iterable[str] = (),
    *,
    dsn: str = DSN,
) -> bool:
    """At Tycho's start: with reports on (and a project to send to), keep crashes from
    now on and send earlier ones in the background. Otherwise delete any waiting.
    Returns whether reports are on for this run."""
    if setting is not True or not dsn:
        forget()
        return False
    never = never_sent(call_me)
    install(allowed, context, never)
    threading.Thread(
        target=_send_quietly, args=(dsn, allowed, never), name="crash-reports", daemon=True
    ).start()
    return True


def _send_quietly(dsn: str, allowed: Callable[[], bool], never: list[str]) -> None:
    try:
        if sent := send_waiting(dsn, allowed, never):
            log.info("Sent %d crash report(s)", sent)
    except Exception:
        log.debug("Couldn't send crash reports", exc_info=True)
