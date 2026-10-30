"""Where a Star Citizen install keeps the files Tycho reads."""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\Program Files\Roberts Space Industries\StarCitizen")
CHANNELS = ("LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX")


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
    def loose_global_ini(self) -> Path:
        """A language override, e.g. from a community pack. Usually absent."""
        return self.folder / "Data" / "Localization" / "english" / "global.ini"

    @property
    def game_log(self) -> Path:
        return self.folder / "Game.log"

    @classmethod
    def find(cls, channel: str = "LIVE", roots: tuple[Path, ...] = (DEFAULT_ROOT,)) -> GameInstall:
        for root in roots:
            install = cls(root / channel)
            if install.data_p4k.is_file():
                return install
        searched = ", ".join(str(r / channel) for r in roots)
        raise FileNotFoundError(f"No Star Citizen {channel} install found in {searched}")
