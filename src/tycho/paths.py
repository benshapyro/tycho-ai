"""Where Tycho keeps its files. The only module that decides file locations (ADR 11).

- Settings roam with the Windows profile: %APPDATA%\\tycho
- Caches, models and logs stay on this PC: %LOCALAPPDATA%\\tycho

TYCHO_CONFIG_DIR and TYCHO_DATA_DIR override them (tests, portable installs);
TYCHO_MODELS overrides the models folder alone (large downloads on another drive).
"""

import os
from pathlib import Path

APP = "tycho"


def config_dir() -> Path:
    if custom := os.environ.get("TYCHO_CONFIG_DIR"):
        return Path(custom)
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP


def data_dir() -> Path:
    if custom := os.environ.get("TYCHO_DATA_DIR"):
        return Path(custom)
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP


def settings_file() -> Path:
    return config_dir() / "settings.toml"


STAR_CITIZEN = "star-citizen"
"""The first game pack, whose player files stay where they were before packs."""


def pack_dir(pack: str = STAR_CITIZEN) -> Path:
    """Where the player's own files for a game pack live (ADR 18). Star Citizen's stay
    in the config folder itself, so nobody's phrases or fleet move."""
    return config_dir() if pack == STAR_CITIZEN else config_dir() / "packs" / pack


def commands_file(pack: str = STAR_CITIZEN) -> Path:
    """The player's own commands and sequences, added to the game's built-in ones (ADR 14)."""
    return pack_dir(pack) / "commands.toml"


def fleet_file(pack: str = STAR_CITIZEN) -> Path:
    """The player's ships (ADR 15), kept by Tycho and editable by hand."""
    return pack_dir(pack) / "fleet.toml"


def cache_dir() -> Path:
    return data_dir() / "cache"


def models_dir() -> Path:
    if custom := os.environ.get("TYCHO_MODELS"):
        return Path(custom)
    return data_dir() / "models"


def knowledge_dir() -> Path:
    """Look-up data downloaded on this PC, such as the ship data (ADR 15)."""
    return data_dir() / "knowledge"


def cuda_dir() -> Path:
    """NVIDIA's CUDA libraries, downloaded by ``tycho gpu install``."""
    return data_dir() / "cuda"


def voice_dir() -> Path:
    """Spoken lines kept as audio, such as the filler while a search runs."""
    return data_dir() / "voice"


def logs_dir() -> Path:
    return data_dir() / "logs"


def rsi_launcher_logs() -> Path:
    """The RSI Launcher's own logs, which name the folder the game is installed in. Read
    only; Tycho never writes there."""
    return (
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "rsilauncher"
        / "logs"
    )
