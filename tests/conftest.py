"""Settings for every test."""

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest

from tests.fakes.fake_keyring import MemoryKeyring
from tycho import paths

REAL_MODELS = paths.models_dir()
"""The developer's real speech model folder, worked out before any test changes the
environment. Only the replay tests (``-m replay``) are given it."""


@pytest.fixture(autouse=True)
def _no_real_keys() -> Iterator[None]:
    """Every test starts with an empty in-memory keyring, never the player's real keys
    (which would also be registered with the log redactor for the rest of the run)."""
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _no_real_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Every test gets its own empty settings, data, logs and models folders, never the
    player's real settings.toml, fleet.toml, logs or downloads (audit, 2026-11-03; #61).
    APPDATA and LOCALAPPDATA move too, so code that builds a path from them directly
    lands in the test's folder as well. The replay tests keep the real speech model."""
    home = tmp_path / "home"
    monkeypatch.setenv("APPDATA", str(home / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "Local"))
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TYCHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TYCHO_MODELS", str(models_folder(request.node, tmp_path)))


def models_folder(node: pytest.Item, tmp_path: Path) -> Path:
    """The speech model folder a test gets: the real one for a replay test, so it can
    load the model, and its own empty one for every other test (#302)."""
    return REAL_MODELS if node.get_closest_marker("replay") is not None else tmp_path / "models"
