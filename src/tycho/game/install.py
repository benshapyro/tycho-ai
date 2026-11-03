"""Where a Star Citizen install keeps the files Tycho reads, and finding it.

The search, first match wins: game_root in settings (added by the caller), the
TYCHO_SC_ROOT variable, the folders the RSI Launcher's log names (most recent first),
the default folder, then common folders on each fixed drive.
"""

import ctypes
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from tycho import paths

DEFAULT_ROOT = Path(r"C:\Program Files\Roberts Space Industries\StarCitizen")
ROOT_VARIABLE = "TYCHO_SC_ROOT"
"""Set to the folder that contains LIVE, for installs outside the default location."""

type Channel = Literal["LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX"]
CHANNELS: tuple[Channel, ...] = get_args(Channel.__value__)

COMMON_FOLDERS = (
    r"Program Files\Roberts Space Industries\StarCitizen",
    r"Roberts Space Industries\StarCitizen",
    r"Games\Roberts Space Industries\StarCitizen",
    r"Games\StarCitizen",
    r"StarCitizen",
)
"""Where players commonly put the game, relative to each fixed drive."""

LAUNCHER_LOGS = ("log.log", "log.old.log")
"""Newest first. The launcher's settings file is encrypted; its log isn't."""

# Lines the launcher writes, with backslashes doubled because each line is JSON:
#   [Launcher::launch] Launching Star Citizen LIVE from (C:\...\StarCitizen\LIVE)
#   Installing Star Citizen LIVE 4.10.2-live.12913470 at C:\...\StarCitizen (type: update, ...
# Greedy, so a folder such as "Program Files (x86)" keeps its brackets.
_LAUNCHED = re.compile(r"Launching Star Citizen [\w-]+ from \((?P<folder>.+)\)")
_INSTALLED = re.compile(r"Installing Star Citizen [\w-]+ \S+ at (?P<root>.+) \(type:")


def search_roots() -> tuple[Path, ...]:
    """Every folder that might contain the game's channel folders, most likely first."""
    custom = os.environ.get(ROOT_VARIABLE)
    roots = (
        *((Path(custom),) if custom else ()),
        *launcher_roots(),
        DEFAULT_ROOT,
        *(Path(drive) / folder for drive in fixed_drives() for folder in COMMON_FOLDERS),
    )
    return _unique(roots)


def launcher_roots(logs: Path | None = None) -> tuple[Path, ...]:
    """The game folders the RSI Launcher's log names, most recent first. It logs the
    folder each time it installs, updates or launches the game."""
    logs = logs or paths.rsi_launcher_logs()
    found: list[Path] = []
    for name in LAUNCHER_LOGS:
        try:
            text = (logs / name).read_text("utf-8", errors="replace")
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            line = line.replace("\\\\", "\\")
            if launched := _LAUNCHED.search(line):
                found.append(Path(launched["folder"]).parent)
            elif installed := _INSTALLED.search(line):
                found.append(Path(installed["root"]))
    return _unique(tuple(found))


def fixed_drives() -> tuple[str, ...]:
    """Local hard drives, e.g. ("C:\\", "D:\\"). Never network, optical or removable
    drives, which can be slow or ask for a disc."""
    if sys.platform != "win32":
        return ()
    kernel32 = ctypes.WinDLL("kernel32")
    mask: int = kernel32.GetLogicalDrives()
    letters = (f"{chr(ord('A') + i)}:\\" for i in range(26) if mask >> i & 1)
    return tuple(d for d in letters if kernel32.GetDriveTypeW(d) == DRIVE_FIXED)


DRIVE_FIXED = 3


def root_from(folder: Path, channel: str = "LIVE") -> Path | None:
    """The folder that contains ``channel``, from whichever level a player gives: the
    channel folder itself, StarCitizen, or Roberts Space Industries. None if none has it."""
    for root in (folder.parent, folder, folder / "StarCitizen"):
        if (root / channel / "Data.p4k").is_file():
            return root
    return None


def _unique(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Without repeats, ignoring case as Windows does, keeping the first of each."""
    seen: set[str] = set()
    kept: list[Path] = []
    for root in roots:
        key = os.path.normcase(os.path.normpath(root))
        if key not in seen:
            seen.add(key)
            kept.append(root)
    return tuple(kept)


@dataclass(frozen=True, slots=True)
class GameInstall:
    """One channel's folder, e.g. ...\\StarCitizen\\LIVE."""

    folder: Path

    @property
    def channel(self) -> str:
        return self.folder.name

    @property
    def data_p4k(self) -> Path:
        return self.folder / "Data.p4k"

    @property
    def actionmaps(self) -> Path:
        """The player's own keybinds: only what they changed from the defaults."""
        return self.folder / "user" / "client" / "0" / "Profiles" / "default" / "actionmaps.xml"

    @property
    def control_profiles(self) -> Path:
        """Where "save/load control settings" keeps exported profiles."""
        return self.folder / "user" / "client" / "0" / "Controls" / "Mappings"

    @property
    def loose_global_ini(self) -> Path:
        """A language override, e.g. from a community pack. Usually absent."""
        return self.folder / "Data" / "Localization" / "english" / "global.ini"

    @property
    def game_log(self) -> Path:
        return self.folder / "Game.log"

    @classmethod
    def find(cls, channel: str = "LIVE", roots: tuple[Path, ...] | None = None) -> GameInstall:
        roots = roots if roots is not None else search_roots()
        for root in roots:
            install = cls(root / channel)
            if install.data_p4k.is_file():
                return install
        raise FileNotFoundError(
            f"No Star Citizen {channel} install found: looked where the RSI Launcher last "
            f"installed or launched it, in {DEFAULT_ROOT}, and in common folders on each "
            f"drive. Set game_root in %APPDATA%\\tycho\\settings.toml to the folder that "
            f"contains {channel} (or the {ROOT_VARIABLE} environment variable)."
        )
