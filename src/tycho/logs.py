"""Logging: a readable console, and a rotating JSON-lines file for diagnosis (ADR 11, #27).

Every line, including tracebacks, passes through ``redact`` before it's written,
so an API key can't reach a log even if one ends up in an error message. Keys
read through ``tycho.credentials`` are registered here and scrubbed by value;
anything shaped like a key is scrubbed by pattern as well.
"""

import json
import logging
import logging.handlers
import re
import threading
from datetime import datetime
from pathlib import Path

from tycho import paths

LOG_FILE = "tycho.jsonl"
_MAX_BYTES = 2_000_000
_BACKUPS = 5
_REDACTED = "[redacted]"

_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic
    re.compile(r"\b(?:sk|csk|sk_)[-_]?[A-Za-z0-9]{20,}"),  # OpenAI-style, Cerebras, ElevenLabs
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|x-api-key|xi-api-key|token|secret|password|authorization)"
        r"([\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]{6,}"
    ),
)
_secrets: set[str] = set()
_lock = threading.Lock()


def register_secret(value: str) -> None:
    """Scrub this exact value from every log line from now on."""
    if len(value) >= 8:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, _REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(_keep_label, text)
    return text


def _keep_label(match: re.Match[str]) -> str:
    """For "api_key=abc123", keep "api_key=" so the line still says what was there."""
    if match.re.groups >= 2:
        return f"{match.group(1)}{match.group(2)}{_REDACTED}"
    if match.re.groups == 1:
        return f"{match.group(1)} {_REDACTED}"
    return _REDACTED


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if record.levelno >= logging.WARNING:
            line = line.replace("  ", f"  {record.levelname.title()}: ", 1)
        return redact(line)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, ensure_ascii=False))


def setup(*, verbose: bool = False, folder: Path | None = None) -> Path:
    """Send Tycho's logs to the console and the log file. Returns the file's path."""
    folder = folder or paths.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler()
    console.setFormatter(ConsoleFormatter())
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    file = logging.handlers.RotatingFileHandler(
        folder / LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    file.setFormatter(JsonFormatter())
    file.setLevel(logging.DEBUG)

    root = logging.getLogger("tycho")
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    root.addHandler(console)
    root.addHandler(file)
    root.setLevel(logging.DEBUG)
    root.propagate = False
    return folder / LOG_FILE
