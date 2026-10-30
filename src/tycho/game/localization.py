"""The game's text: turning label keys like "@ui_CIToggleLandingSystem" into words."""

from collections.abc import Mapping

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
