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


def cache_dir() -> Path:
    return data_dir() / "cache"


def models_dir() -> Path:
    if custom := os.environ.get("TYCHO_MODELS"):
        return Path(custom)
    return data_dir() / "models"


def logs_dir() -> Path:
    return data_dir() / "logs"
