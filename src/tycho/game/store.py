"""Loading keybinds, caching the slow part, and reloading when they change.

Reading the defaults out of Data.p4k takes several seconds, so they're cached
per game build (keyed by Data.p4k's size and modification time). The player's
actionmaps.xml is small and re-read whenever it changes on disk, which is when
the game saves new bindings.

Loading and refreshing block on disk I/O. From async code, call them with
``asyncio.to_thread``.
"""

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from tycho import paths
from tycho.game import cryxml, localization
from tycho.game.install import GameInstall
from tycho.game.keybinds import (
    DEFAULTS_P4K_PATH,
    Action,
    ActivationMode,
    Keybinds,
    apply_profile,
    parse_activation_modes,
    parse_defaults,
)
from tycho.game.p4k import P4kArchive

type _Stamp = tuple[int, int] | None


class GameDataError(Exception):
    """The game's files couldn't be read: missing, mid-patch, or corrupt."""


def default_cache_dir() -> Path:
    return paths.cache_dir()


class KeybindStore:
    """The current keybinds for one install.

    Create it with ``KeybindStore.load(install)``, then call ``refresh()``
    regularly to pick up changes. If a refresh fails (the game is patching, or
    has the file locked), the last good keybinds are kept and ``last_error``
    says why.
    """

    def __init__(self, install: GameInstall, cache_dir: Path | None = None) -> None:
        self.install = install
        self.cache_dir = cache_dir or default_cache_dir()
        self.keybinds = Keybinds([])
        self.loaded = False
        self.last_error: str | None = None
        self._defaults: list[Action] = []
        self._modes: dict[str, ActivationMode] = {}
        self._defaults_stamp: _Stamp = None
        self._profile_stamp: _Stamp = None
        self._ini_stamp: _Stamp = None

    @classmethod
    def load(cls, install: GameInstall, cache_dir: Path | None = None) -> KeybindStore:
        """A store with keybinds loaded. Raises GameDataError if the game's files can't be read."""
        store = cls(install, cache_dir)
        store.refresh()
        if not store.loaded:
            raise GameDataError(store.last_error or "keybinds could not be loaded")
        return store

    @property
    def defaults(self) -> list[Action]:
        """The game's default bindings, before the player's changes."""
        return list(self._defaults)

    def refresh(self) -> bool:
        """Reload whatever changed on disk. Returns True if the keybinds changed."""
        game, profile = _stamp(self.install.data_p4k), _stamp(self.install.actionmaps)
        ini = _stamp(self.install.loose_global_ini)
        if self.loaded and (game, profile, ini) == (
            self._defaults_stamp,
            self._profile_stamp,
            self._ini_stamp,
        ):
            return False
        try:
            if not self.loaded or (game, ini) != (self._defaults_stamp, self._ini_stamp):
                self._defaults = self._load_defaults(game)
                self._defaults_stamp, self._ini_stamp = game, ini
            actions = self._defaults
            if profile is not None:
                root = ET.parse(self.install.actionmaps).getroot()  # noqa: S314 - local game file; expat guards entity expansion
                actions = apply_profile(actions, root)
        except (ET.ParseError, OSError, ValueError, GameDataError) as e:
            # Mid-write, locked by the game, or mid-patch: keep what we had and retry later.
            self.last_error = f"{type(e).__name__}: {e}"
            return False
        self._profile_stamp = profile
        self.keybinds = Keybinds(actions, self._modes)
        self.loaded = True
        self.last_error = None
        return True

    def _load_defaults(self, game: _Stamp) -> list[Action]:
        if game is None:
            raise GameDataError(f"{self.install.data_p4k} not found")
        build = f"{game[0]}-{game[1]}"
        defaults_cache = self.cache_dir / f"defaultProfile-{build}.xml"
        ini_cache = self.cache_dir / f"global-{build}.ini"
        loose_ini = self.install.loose_global_ini

        root = _read_cached_xml(defaults_cache)
        need_ini = not loose_ini.is_file() and not ini_cache.is_file()
        if root is None or need_ini:
            wanted = ([DEFAULTS_P4K_PATH] if root is None else []) + (
                [localization.P4K_PATH] if need_ini else []
            )
            archive = P4kArchive(self.install.data_p4k)
            entries = archive.find(wanted)
            if absent := [name for name in wanted if name not in entries]:
                raise GameDataError(f"{', '.join(absent)} not found in {self.install.data_p4k}")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            if root is None:
                root = cryxml.decode(archive.read(entries[DEFAULTS_P4K_PATH]))
                _write_atomic(defaults_cache, ET.tostring(root, encoding="utf-8"))
            if need_ini:
                _write_atomic(ini_cache, archive.read(entries[localization.P4K_PATH]))
            self._prune(keep=build)

        ini_path = loose_ini if loose_ini.is_file() else ini_cache
        strings = localization.parse_global_ini(ini_path.read_text("utf-8-sig", errors="replace"))
        self._modes = parse_activation_modes(root)
        return parse_defaults(root, strings)

    def _prune(self, keep: str) -> None:
        """Delete caches from other game builds."""
        for pattern in ("defaultProfile-*.xml", "global-*.ini"):
            for path in self.cache_dir.glob(pattern):
                if keep not in path.name:
                    path.unlink(missing_ok=True)


def _read_cached_xml(path: Path) -> ET.Element | None:
    """A cached XML file, or None if it's missing or corrupt (a corrupt one is deleted)."""
    if not path.is_file():
        return None
    try:
        return ET.parse(path).getroot()  # noqa: S314 - our own cache file
    except ET.ParseError:
        path.unlink(missing_ok=True)
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via a temporary file, so an interrupted write never leaves a partial file."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _stamp(path: Path) -> _Stamp:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns
