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

SERVICE = "tycho"
KNOWN = ("anthropic", "cerebras", "typesafe", "elevenlabs")


def get(name: str) -> str | None:
    """The stored key, or None if there isn't one."""
    return keyring.get_password(SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete(name: str) -> bool:
    """Remove a key. Returns False if there was none."""
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return False
    return True


def masked(value: str) -> str:
    """Enough of a key to tell which one it is, never the whole thing."""
    return f"{value[:7]}...{value[-4:]}" if len(value) > 16 else "***"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tycho.credentials", description=__doc__)
    parser.add_argument("command", choices=["set", "delete", "check"])
    parser.add_argument("name", choices=KNOWN)
    args = parser.parse_args(argv)

    if args.command == "set":
        value = getpass.getpass(f"Paste your {args.name} API key (input is hidden): ").strip()
        if not value:
            print("Nothing entered; no key stored.")
            return 1
        set_secret(args.name, value)
        print(f"Stored {args.name} key {masked(value)} in Windows Credential Manager.")
    elif args.command == "delete":
        print("Removed." if delete(args.name) else "No key was stored.")
    else:
        value = get(args.name)
        print(f"Stored: {masked(value)}" if value else "No key stored.")
        return 0 if value else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
