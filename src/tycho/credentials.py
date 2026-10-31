"""API keys, kept in the operating system's credential store (Windows Credential Manager).

Never in files, environment files or logs. To store or remove a key, run one of:

    uv run python -m tycho.credentials set anthropic
    uv run python -m tycho.credentials delete anthropic
    uv run python -m tycho.credentials check anthropic
"""

import argparse
import getpass
import sys

import keyring
import keyring.errors

from tycho import logs

SERVICE = "tycho"
KNOWN = ("anthropic", "cerebras", "typesafe", "elevenlabs")
# Public prefixes that say which provider a key is for, and are safe to show.
_PUBLIC_PREFIXES = ("sk-ant-", "csk-", "sk_")


class CredentialStoreError(Exception):
    """The operating system's credential store is unavailable or refused the request."""


def get(name: str) -> str | None:
    """The stored key, or None if there isn't one. Registered with the log redactor."""
    try:
        value = keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError as e:
        raise CredentialStoreError(_explain(e)) from e
    if value:
        logs.register_secret(value)
    return value


def set_secret(name: str, value: str) -> None:
    try:
        keyring.set_password(SERVICE, name, value)
    except keyring.errors.KeyringError as e:
        raise CredentialStoreError(_explain(e)) from e


def delete(name: str) -> bool:
    """Remove a key. Returns False if there was none."""
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as e:
        raise CredentialStoreError(_explain(e)) from e
    return True


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
    parser = argparse.ArgumentParser(prog="python -m tycho.credentials", description=__doc__)
    parser.add_argument("command", choices=["set", "delete", "check"])
    parser.add_argument("name", choices=KNOWN)
    args = parser.parse_args(argv)
    try:
        return _run(args.command, args.name)
    except CredentialStoreError as e:
        print(e, file=sys.stderr)
        return 2


def _run(command: str, name: str) -> int:
    if command == "set":
        value = getpass.getpass(f"Paste your {name} API key (input is hidden): ").strip()
        if not value:
            print("Nothing entered; no key stored.")
            return 1
        set_secret(name, value)
        print(f"Stored {name} key {masked(value)} in Windows Credential Manager.")
    elif command == "delete":
        print("Removed." if delete(name) else "No key was stored.")
    else:
        value = get(name)
        print(f"Stored: {masked(value)}" if value else "No key stored.")
        return 0 if value else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
