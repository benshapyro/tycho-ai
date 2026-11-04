"""Settings for every test."""

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest

from tests.fakes.fake_keyring import MemoryKeyring
from tycho import paths


@pytest.fixture(autouse=True)
def _no_real_keys() -> Iterator[None]:
    """Every test starts with an empty in-memory keyring, never the player's real keys
    (which would also be registered with the log redactor for the rest of the run)."""
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _no_real_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own empty settings and data folders, never the player's real
    settings.toml, fleet.toml or logs (audit, 2026-11-03). The speech model stays where
    it is, for the replay tests; nothing else reads it without a folder of its own."""
    monkeypatch.setenv("TYCHO_MODELS", str(paths.models_dir()))
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TYCHO_DATA_DIR", str(tmp_path / "data"))
