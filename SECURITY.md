# Security policy

Tycho handles API keys, sends keypresses and can capture your screen, so we take security reports seriously.

## Reporting a vulnerability

Please **don't** open a public issue. Use GitHub's [private vulnerability reporting](../../security/advisories/new) for this repository instead.

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to acknowledge reports within 7 days.

## Supported versions

Tycho is pre-alpha. Only the latest commit on `main` is supported.

## Design commitments

- API keys are stored in Windows Credential Manager, never in config files or logs.
- Any local server binds to `127.0.0.1` only and requires a per-session token.
- Tycho never reads or writes game memory, and never injects into the game process.
