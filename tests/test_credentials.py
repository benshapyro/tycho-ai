import sys
from collections.abc import Iterator

import keyring
import keyring.backend
import keyring.errors
import pytest

from tycho import credentials


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.store.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture
def memory() -> Iterator[MemoryKeyring]:
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def test_set_get_delete(memory: MemoryKeyring) -> None:
    assert credentials.get("anthropic") is None
    credentials.set_secret("anthropic", "sk-ant-test-123456789")
    assert credentials.get("anthropic") == "sk-ant-test-123456789"
    assert memory.store == {("tycho", "anthropic"): "sk-ant-test-123456789"}
    assert credentials.delete("anthropic")
    assert not credentials.delete("anthropic")


def test_masked_never_shows_the_whole_key() -> None:
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
    shown = credentials.masked(key)
    assert shown == "sk-ant-...wxyz"
    assert key not in shown
    assert credentials.masked("short") == "***"
    # A key with no known prefix shows only its last four characters.
    assert credentials.masked("0123456789abcdef0123456789abcdef") == "...cdef"


class BrokenKeyring(MemoryKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringLocked("locked")


def test_an_unavailable_store_is_a_clear_error(capsys: pytest.CaptureFixture[str]) -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(BrokenKeyring())
    try:
        with pytest.raises(credentials.CredentialStoreError, match="Credential Manager"):
            credentials.get("anthropic")
        assert credentials.main(["check", "anthropic"]) == 2
        assert "Credential Manager" in capsys.readouterr().err
    finally:
        keyring.set_keyring(previous)


def test_cli_set_reads_hidden_input(
    memory: MemoryKeyring, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_getpass(prompt: str) -> str:
        return "  sk-ant-api03-secretsecret1234  "

    monkeypatch.setattr("getpass.getpass", fake_getpass)
    assert credentials.main(["set", "anthropic"]) == 0
    assert credentials.get("anthropic") == "sk-ant-api03-secretsecret1234"
    output = capsys.readouterr().out
    assert "secretsecret" not in output
    assert credentials.main(["check", "anthropic"]) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_a_lost_backend_falls_back_to_windows_credential_manager() -> None:
    # What a frozen build sees when keyring can't find its backends (#56).
    from keyring.backends.fail import Keyring as Failing
    from keyring.backends.Windows import WinVaultKeyring

    previous = keyring.get_keyring()
    keyring.set_keyring(Failing())
    try:
        credentials._store()  # pyright: ignore[reportPrivateUsage]
        assert isinstance(keyring.get_keyring(), WinVaultKeyring)
    finally:
        keyring.set_keyring(previous)


def test_a_chosen_backend_is_left_alone(memory: MemoryKeyring) -> None:
    credentials._store()  # pyright: ignore[reportPrivateUsage]
    assert keyring.get_keyring() is memory


def test_tycho_key_stores_and_checks_a_key(
    memory: MemoryKeyring, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tycho import app

    def typed(prompt: str = "") -> str:
        return "sk-ant-test-123456789"

    monkeypatch.setattr(credentials.getpass, "getpass", typed)
    assert app.main(["key", "set", "anthropic"]) == 0
    assert memory.store == {("tycho", "anthropic"): "sk-ant-test-123456789"}
    assert app.main(["key", "check", "anthropic"]) == 0
    out = capsys.readouterr().out
    assert "sk-ant-" in out
    assert "123456789" not in out  # only ever masked
    assert app.main(["key", "delete", "anthropic"]) == 0
    assert app.main(["key", "check", "anthropic"]) == 1


def test_every_use_of_the_store_sets_up_the_backend_first(
    memory: MemoryKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Getting, setting and deleting all go through _store(), or an installed build
    # whose keyring lost its backend would fail on that one (#56, the #78 review).
    calls: list[str] = []
    monkeypatch.setattr(credentials, "_store", lambda: calls.append("store"))
    credentials.set_secret("anthropic", "sk-ant-test-123456789")
    credentials.get("anthropic")
    credentials.delete("anthropic")
    assert calls == ["store", "store", "store"]
