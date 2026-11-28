"""Settings as TOML: each value with its description as a comment (ADR 5). Out of
``settings`` so the schema stays under ADR 19's line limit."""

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def to_toml(settings: BaseModel) -> str:
    """The settings as TOML, each value with its description as a comment. Unset values omitted."""
    lines = ["# Tycho settings. Edit by hand, by voice, or in the settings screen.", ""]
    fields = type(settings).model_fields
    for name, value in settings.model_dump().items():
        if value is None:
            lines.append(f"# {name} = (not set). {fields[name].description}")
            continue
        lines += [f"# {fields[name].description}", f"{name} = {toml_value(value)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def read_toml(path: Path) -> dict[str, Any] | None:
    """The settings file's entries, or None if there's no file. Raises ``ValueError``
    with a message a player can act on if it can't be read."""
    if not path.exists():
        return None
    try:
        # utf-8-sig: Notepad and PowerShell can save a byte-order mark, which TOML rejects.
        text = path.read_text("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"Couldn't read {path}: {e}") from e
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Couldn't read {path}: {e}{_folder_hint(text, e.lineno)}") from e


def _folder_hint(text: str, line: int) -> str:
    """How to write a Windows folder, when the line TOML refused has one in double
    quotes: there a backslash starts an escape, so "D:\\Games" can't be read."""
    lines = text.splitlines()
    said = lines[line - 1] if 0 < line <= len(lines) else ""
    if "\\" not in said or '"' not in said:
        return ""
    return (
        ". A Windows folder goes in single quotes, so its backslashes are kept: "
        "game_root = 'D:\\Games\\StarCitizen'"
    )


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: an interrupted save never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
