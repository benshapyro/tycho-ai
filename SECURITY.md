# Security policy

Tycho handles API keys, sends keypresses and can capture your screen, so we take security reports seriously.

## Reporting a vulnerability

Please **don't** open a public issue. Use GitHub's [private vulnerability reporting](../../security/advisories/new) for this repository instead.

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to acknowledge reports within 7 days.

## Supported versions

Tycho is pre-alpha. Only the latest commit on `main` is supported.

## Design commitments

- API keys (Anthropic, ElevenLabs, and optionally Cerebras and TypeSafe) are stored in Windows Credential Manager through `tycho.credentials`, never in config files or logs.
- Audio never leaves your PC. The text that goes to each provider is listed in the README under "What leaves your PC".
- Recordings and benchmark results stay in the local `recordings/` folder, which git ignores.
- Any local server binds to `127.0.0.1` only and requires a per-session token.
- Tycho never reads or writes game memory, and never injects into the game process.
