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
