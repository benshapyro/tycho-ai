"""The game's text: turning label keys like "@ui_CIToggleLandingSystem" into words."""

from collections.abc import Callable, Mapping
from pathlib import Path

P4K_PATH = r"Data\Localization\english\global.ini"


def parse_global_ini(text: str) -> dict[str, str]:
    """Parse global.ini into {lowercase key: text}. Keys may carry suffixes like ",P"."""
    strings: dict[str, str] = {}
    for line in text.lstrip("﻿").splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith(";"):
            strings[key.split(",")[0].strip().lower()] = value.replace("\\n", " ").strip()
    return strings


def resolve(label: str, strings: Mapping[str, str]) -> str:
    """The text for a label like "@ui_X", or "" if there is none."""
    if not label.startswith("@"):
        return label
    return strings.get(label[1:].lower(), "")


def lazy_names(*files: Callable[[], Path | None]) -> Callable[[str], str]:
    """The game's text for a key (lower case, no "@"), read from the first of ``files``
    that exists, on the first look-up only: global.ini is large and most sessions never
    need it. "" when there's no such key or no file."""
    strings: dict[str, str] | None = None

    def name(key: str) -> str:
        nonlocal strings
        if strings is None:
            strings = {}
            for find in files:
                try:
                    if (path := find()) is not None and path.is_file():
                        strings = parse_global_ini(path.read_text("utf-8-sig", errors="replace"))
                        break
                except OSError:
                    continue
        return strings.get(key.lower(), "")

    return name
