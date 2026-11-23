# Security policy

Tycho handles API keys and sends keypresses, so we take security reports seriously.

## Reporting a vulnerability

Please **don't** open a public issue. Use GitHub's [private vulnerability reporting](../../security/advisories/new) for this repository instead.

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to acknowledge reports within 7 days.

## Supported versions

Tycho is pre-alpha. Only the latest release, and the latest commit on `main`, are supported.

## Design commitments

- API keys (Anthropic, ElevenLabs and OpenRouter, plus Cerebras and TypeSafe for benchmarks only) are stored in Windows Credential Manager through `tycho.credentials`, never in config files or logs.
- Audio never leaves your PC. The text that goes to each provider is listed in the README under "What leaves your PC". A picture of the game window is sent to the brain only if the player turned looking on, and only when they ask; it is never saved.
- The player's memory notes, owned blueprints and history (if kept) are files on their PC. Notes go to the brain with each request, the others only inside an answer the player asked for, and none of them ever goes in a crash report.
- Tycho tracks nothing about how you use it. Crash reports are sent only if you say yes, hold only an allowlisted set of fields (the error, Tycho's own code, versions, the game and the brain provider), and never what you said, your keys or anything that identifies you or your PC.
- Recordings and benchmark results stay in the local `recordings/` folder, which git ignores.
- Any local server binds to `127.0.0.1` only and requires a per-session token.
- Tycho never reads or writes game memory, and never injects into the game process.
