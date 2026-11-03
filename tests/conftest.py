"""Settings for every test."""

from collections.abc import Iterator

import keyring
import pytest

from tests.fakes.fake_keyring import MemoryKeyring


@pytest.fixture(autouse=True)
def _no_real_keys() -> Iterator[None]:
    """Every test starts with an empty in-memory keyring, never the player's real keys
    (which would also be registered with the log redactor for the rest of the run)."""
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)
