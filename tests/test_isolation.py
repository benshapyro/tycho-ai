"""No test can touch a developer's real settings, files or keys (#61).

These check the autouse fixtures in conftest.py: without them every check here fails."""

import os
from pathlib import Path

import keyring
import pytest

from tests.fakes.fake_keyring import MemoryKeyring
from tycho import credentials, paths


def test_every_folder_tycho_writes_to_is_the_tests_own(tmp_path: Path) -> None:
    for folder in (
        paths.config_dir(),
        paths.data_dir(),
        paths.logs_dir(),
        paths.models_dir(),
        paths.cache_dir(),
        paths.knowledge_dir(),
        paths.voice_dir(),
        paths.cuda_dir(),
    ):
        assert folder.is_relative_to(tmp_path), folder
    for name in ("APPDATA", "LOCALAPPDATA"):
        assert Path(os.environ[name]).is_relative_to(tmp_path), name


def test_keys_go_to_a_memory_keyring_that_starts_empty() -> None:
    assert isinstance(keyring.get_keyring(), MemoryKeyring)
    assert all(credentials.get(name) is None for name in credentials.KNOWN)


@pytest.mark.replay
def test_only_the_replay_tests_keep_the_real_speech_model(tmp_path: Path) -> None:
    assert not paths.models_dir().is_relative_to(tmp_path)
    assert paths.config_dir().is_relative_to(tmp_path)


def test_the_models_folder_is_real_only_for_a_replay_test(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    # The replay test above never runs in CI (-m "not replay"), so the choice is checked
    # here, with and without the marker (#302).
    from tests.conftest import REAL_MODELS, models_folder

    node = request.node
    assert isinstance(node, pytest.Item)
    assert models_folder(node, tmp_path) == tmp_path / "models"
    node.add_marker(pytest.mark.replay)
    assert models_folder(node, tmp_path) == REAL_MODELS
