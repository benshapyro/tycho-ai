"""The installer script (packaging/tycho.iss), as far as it can be checked without
Inno Setup: CI builds it."""

from pathlib import Path

from tycho.runtime import instance

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "tycho.iss"


def _setup_values() -> dict[str, str]:
    """The [Setup] section's ``Name=value`` lines."""
    values: dict[str, str] = {}
    section = ""
    for raw in SCRIPT.read_text("utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[Setup]" and "=" in line and not line.startswith((";", "#")):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def test_setup_waits_for_a_running_tycho_to_quit() -> None:
    # A running Tycho locks its files, and the tray app has no window for Windows to
    # close: without this, an upgrade fails one locked file at a time (#333).
    assert _setup_values()["AppMutex"] == instance.DEFAULT_NAME
