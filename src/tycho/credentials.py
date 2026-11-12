"""API keys, kept in the operating system's credential store (Windows Credential Manager).

Never in files, environment files or logs. To store or remove a key, run one of:

    uv run tycho key set anthropic
    uv run tycho key delete anthropic
    uv run tycho key check anthropic

The benchmarks' own keys (``BENCH``) aren't offered to players; developers store them
with ``uv run python -m tycho.credentials set cerebras``.
"""

import argparse
import getpass
import sys

import keyring
import keyring.backends.fail
import keyring.errors

from tycho import logs

SERVICE = "tycho"
PRODUCT = ("anthropic", "elevenlabs")
"""Keys Tycho itself uses, offered to players by ``tycho key``."""
BENCH = ("cerebras", "typesafe", "openrouter")
"""Keys only the benchmarks in tools/bench use (#61), stored with
``uv run python -m tycho.credentials set <name>``. OpenRouter moves to PRODUCT when a
player can pick it as the brain's provider (#112)."""
KNOWN = PRODUCT + BENCH
# Public prefixes that say which provider a key is for, and are safe to show.
_PUBLIC_PREFIXES = ("sk-ant-", "sk-or-v1-", "csk-", "sk_")


class CredentialStoreError(Exception):
    """The operating system's credential store is unavailable or refused the request."""


def _store() -> None:
    """Use Windows Credential Manager when keyring couldn't find a backend itself.

    keyring finds backends through package entry points, which frozen (installed)
    builds often lose: it then falls back to one that fails every call (#56).
    A backend chosen on purpose, such as a test's, is left alone."""
    if sys.platform == "win32" and isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring):
        from keyring.backends.Windows import WinVaultKeyring

        keyring.set_keyring(WinVaultKeyring())


def get(name: str) -> str | None:
    """The stored key, or None if there isn't one. Registered with the log redactor."""
    _store()
    try:
        value = keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError as e:
        raise CredentialStoreError(_explain(e)) from e
    if value:
        logs.register_secret(value)
    return value


def set_secret(name: str, value: str) -> None:
    _store()
    try:
        keyring.set_password(SERVICE, name, value)
    except keyring.errors.KeyringError as e:
        raise CredentialStoreError(_explain(e)) from e


def delete(name: str) -> bool:
    """Remove a key. Returns False if there was none."""
    _store()
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as e:
        raise CredentialStoreError(_explain(e)) from e
    return True


def usable(value: str) -> bool:
    """Whether a stored key can be sent. A character keys don't have, such as a
    zero-width space pasted with it, can't go in a request header (#246, #248)."""
    return value.isascii()


UNUSABLE = (
    "has a character keys don't have, maybe an invisible one, so Tycho can't use it. Store it again"
)


def masked(value: str) -> str:
    """Enough of a key to tell which one it is, never the whole thing.

    Shows a known public prefix (e.g. "sk-ant-") and the last four characters.
    """
    if len(value) <= 12:
        return "***"
    prefix = next((p for p in _PUBLIC_PREFIXES if value.startswith(p)), "")
    return f"{prefix}...{value[-4:]}"


def _explain(error: Exception) -> str:
    return (
        "Couldn't use Windows Credential Manager to store or read API keys "
        f"({type(error).__name__}). Check that you're signed in to Windows normally."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tycho key", description=__doc__)
    parser.add_argument("command", choices=["set", "delete", "check"])
    parser.add_argument("name", choices=KNOWN)
    args = parser.parse_args(argv)
    try:
        return _run(args.command, args.name)
    except CredentialStoreError as e:
        print(e, file=sys.stderr)
        return 2


NOT_A_TERMINAL = (
    "Run this in a terminal window, where you can paste the key: it's hidden as you type "
    "and stored only in Windows Credential Manager. Tycho doesn't read a key piped in by a "
    "script or an AI agent, so it never ends up in a transcript. An agent can open a window "
    'for you:\n  start "Tycho key" cmd /k tycho key set {name}'
)


def _interactive() -> bool:
    """Whether a person is typing: a key piped in could be sitting in a log or transcript."""
    return sys.stdin.isatty()


def _run(command: str, name: str) -> int:
    if command == "set":
        if not _interactive():
            print(NOT_A_TERMINAL.format(name=name), file=sys.stderr)
            return 2
        value = getpass.getpass(f"Paste your {name} API key (input is hidden): ").strip()
        if not value:
            print("Nothing entered; no key stored.")
            return 1
        if not usable(value):  # e.g. a zero-width space copied from a web page (#246)
            print(
                "That key has a character keys don't have, maybe an invisible one; "
                "no key stored. Copy it again.",
                file=sys.stderr,
            )
            return 1
        set_secret(name, value)
        print(f"Stored {name} key {masked(value)} in Windows Credential Manager.")
    elif command == "delete":
        print("Removed." if delete(name) else "No key was stored.")
    else:
        value = get(name)
        if value and not usable(value):
            print(f"Stored, but unusable: this key {UNUSABLE}: tycho key set {name}")
            return 1
        print(f"Stored: {masked(value)}" if value else "No key stored.")
        return 0 if value else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
