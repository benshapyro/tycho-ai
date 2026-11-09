import sys
from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.errors
import pytest

from tests.fakes.fake_keyring import MemoryKeyring
from tycho import credentials


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
    monkeypatch.setattr(credentials, "_interactive", lambda: True)
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
    monkeypatch.setattr(credentials, "_interactive", lambda: True)
    assert app.main(["key", "set", "anthropic"]) == 0
    assert memory.store == {("tycho", "anthropic"): "sk-ant-test-123456789"}
    assert app.main(["key", "check", "anthropic"]) == 0
    out = capsys.readouterr().out
    assert "sk-ant-" in out
    assert "123456789" not in out  # only ever masked
    assert app.main(["key", "delete", "anthropic"]) == 0
    assert app.main(["key", "check", "anthropic"]) == 1


def test_tycho_key_check_says_a_stored_key_with_a_hidden_character_is_unusable(
    memory: MemoryKeyring, capsys: pytest.CaptureFixture[str]
) -> None:
    # Stored before `tycho key set` refused such keys, Tycho ignores it: the check
    # mustn't say "Stored" as if it were in use (#250).
    credentials.set_secret("elevenlabs", "sk_" + "a" * 30 + "\u200b")
    assert credentials.main(["check", "elevenlabs"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("Stored, but unusable")
    assert "tycho key set elevenlabs" in out
    assert "aaaa" not in out


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


def test_no_test_can_reach_the_real_credential_manager() -> None:
    # tests/conftest.py: a test of `tycho check` read the real keys (audit, 2026-11-03).
    assert isinstance(keyring.get_keyring(), MemoryKeyring)
    assert credentials.get("anthropic") is None


def test_no_test_can_reach_the_real_settings_or_data(tmp_path: Path) -> None:
    # tests/conftest.py: `tycho key set` read the real settings.toml (audit, 2026-11-03).
    from tycho import paths

    assert paths.settings_file().is_relative_to(tmp_path)
    assert paths.data_dir().is_relative_to(tmp_path)


def test_a_piped_key_is_refused_so_it_never_sits_in_a_transcript(
    memory: MemoryKeyring, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An agent running `tycho key set` with the key on stdin would leave it in its
    # transcript; it should open a terminal window for the player instead.
    def piped(prompt: str = "") -> str:
        return "sk-ant-api03-secretsecret1234"

    monkeypatch.setattr(credentials.getpass, "getpass", piped)
    monkeypatch.setattr(credentials, "_interactive", lambda: False)
    assert credentials.main(["set", "anthropic"]) == 2
    assert memory.store == {}
    err = capsys.readouterr().err
    assert 'start "Tycho key" cmd /k tycho key set anthropic' in err


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("elevenlabs", "sk_" + "a" * 30 + "\u200b"),  # #246
        ("anthropic", "sk-ant-api03-" + "a" * 30 + "\u200b"),  # #248
    ],
)
def test_tycho_key_set_refuses_a_key_with_a_hidden_character(
    memory: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    key: str,
) -> None:
    # It can't go in a request header, so it would stop Tycho starting (#246).
    from tycho import app

    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt="": key)
    monkeypatch.setattr(credentials, "_interactive", lambda: True)
    assert app.main(["key", "set", name]) == 1
    assert memory.store == {}
    assert "invisible" in capsys.readouterr().err
