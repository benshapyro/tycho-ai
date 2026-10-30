"""Loading keybinds, caching the slow part, and reloading when they change.

Reading the defaults out of Data.p4k takes several seconds, so they're cached
per game build (keyed by Data.p4k's size and modification time). The player's
actionmaps.xml is small and re-read whenever it changes on disk, which is when
the game saves new bindings.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from ultimo.game import cryxml, localization
from ultimo.game.install import GameInstall
from ultimo.game.keybinds import DEFAULTS_P4K_PATH, Action, Keybinds, apply_profile, parse_defaults
from ultimo.game.p4k import P4kArchive

type _Stamp = tuple[int, int] | None


def default_cache_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ultimo" / "cache"


class KeybindStore:
    """The current keybinds for one install. Call ``refresh()`` regularly to pick up changes."""

    def __init__(self, install: GameInstall, cache_dir: Path | None = None) -> None:
        self.install = install
        self.cache_dir = cache_dir or default_cache_dir()
        self._defaults: list[Action] = []
        self._defaults_stamp: _Stamp = None
        self._profile_stamp: _Stamp = None
        self._ini_stamp: _Stamp = None
        self.keybinds = Keybinds([])
        self.refresh()

    def refresh(self) -> bool:
        """Reload whatever changed on disk. Returns True if the keybinds changed."""
        game, profile = _stamp(self.install.data_p4k), _stamp(self.install.actionmaps)
        ini = _stamp(self.install.loose_global_ini)
        if (game, profile, ini) == (self._defaults_stamp, self._profile_stamp, self._ini_stamp):
            return False
        if (game, ini) != (self._defaults_stamp, self._ini_stamp):
            self._defaults = self._load_defaults(game)
            self._defaults_stamp, self._ini_stamp = game, ini
        actions = self._defaults
        if profile is not None:
            try:
                root = ET.parse(self.install.actionmaps).getroot()  # noqa: S314 - local game file
            except ET.ParseError:
                return False  # caught mid-write by the game; the next refresh will retry
            actions = apply_profile(actions, root)
        self._profile_stamp = profile
        self.keybinds = Keybinds(actions)
        return True

    def _load_defaults(self, game: _Stamp) -> list[Action]:
        if game is None:
            raise FileNotFoundError(f"{self.install.data_p4k} not found")
        build = f"{game[0]}-{game[1]}"
        defaults_cache = self.cache_dir / f"defaultProfile-{build}.xml"
        ini_cache = self.cache_dir / f"global-{build}.ini"

        missing: list[str] = []
        if not defaults_cache.is_file():
            missing.append(DEFAULTS_P4K_PATH)
        loose_ini = self.install.loose_global_ini
        if not loose_ini.is_file() and not ini_cache.is_file():
            missing.append(localization.P4K_PATH)
        if missing:
            archive = P4kArchive(self.install.data_p4k)
            entries = archive.find(missing)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            if DEFAULTS_P4K_PATH in missing:
                root = cryxml.decode(archive.read(entries[DEFAULTS_P4K_PATH]))
                defaults_cache.write_bytes(ET.tostring(root, encoding="utf-8"))
            if localization.P4K_PATH in missing:
                ini_cache.write_bytes(archive.read(entries[localization.P4K_PATH]))

        ini_path = loose_ini if loose_ini.is_file() else ini_cache
        strings = localization.parse_global_ini(ini_path.read_text("utf-8-sig", errors="replace"))
        root = ET.parse(defaults_cache).getroot()  # noqa: S314 - written by us above
        return parse_defaults(root, strings)


def _stamp(path: Path) -> _Stamp:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns
