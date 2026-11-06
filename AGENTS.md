# AGENTS.md

Instructions for AI coding agents working on Tycho, an unofficial voice co-pilot for Star Citizen. Read [docs/architecture.md](docs/architecture.md) for what we're building, and [docs/adr](docs/adr) for why.

## Commands

- Install: `uv sync`
- Test: `uv run pytest`
- Build the installed app: `uv sync --group build`, then `uv run pyinstaller packaging/tycho.spec --noconfirm` (dist/tycho/tycho.exe), then `uv run python tools/notices.py dist/tycho` for THIRD-PARTY-NOTICES.txt (it fails on any GPL, LGPL or non-commercial licence). The installer is built in CI (`.github/workflows/build.yml`) with Inno Setup from `packaging/tycho.iss`. The build must never include CUDA libraries, pygame or pysdl2-dll's DLLs other than SDL2.dll: CI fails if it does.
- Lint: `uv run ruff check`
- Format: `uv run ruff format`
- Type check: `uv run pyright` (strict mode)
- Replay test (needs the speech model and local recordings; not in CI): `uv run pytest -m replay -s`
- End-to-end self-test through real Windows input, without the game (only when nobody is using the PC): `uv run python tools/spike/selftest.py`

Use `uv`. Never use `pip` or a bare `python`.

## Done means

All four of these pass: `ruff format --check`, `ruff check`, `pyright`, and `pytest`. New behaviour has tests.

## Hard rules

- Never copy or port code from GPL-licensed projects, including Wingman AI. Tycho is Apache-2.0.
- Never read or write game memory, inject into the game process, or hook DirectX.
- Every action must go through the safety gate, be started by the user and have a fixed end. No loops or timers acting alone.
- Never write API keys to files or logs. Use `tycho.credentials` (src/tycho/credentials.py), which stores them in Windows Credential Manager; add new provider names to `KNOWN`.
- The safety gate is independent of every model: no router or brain output may skip a confirm-grade action's confirmation (architecture 4.7, ADR 10).
- Any local server binds to `127.0.0.1` and requires a token.

## Where things are

- `src/tycho/ptt/`: push-to-talk on keyboard, mouse and joystick buttons.
- `src/tycho/audio/`: microphone capture and push-to-talk clips.
- `src/tycho/game/`: the game install, `Data.p4k` and CryXmlB readers, keybinds, the keybind store, profile building, and the `Game.log` reader.
- `src/tycho/lexicon/`: the game-term corrector and its term list.
- `src/tycho/commands/`: the fast-path command matcher, its command list and sequences, and merging in the player's own `commands.toml` (ADR 14).
- `src/tycho/input/`: the hands. Key codes, press planning from the game's activation modes, the presser (focus-guarded, cancellable, always releases), and the Windows sender.
- `src/tycho/stt/`: speech to text. Pinned, checksummed model downloads (`ModelStore`), choosing the GPU or CPU, and the Parakeet transcriber.
- `src/tycho/knowledge/`: look-ups (ADR 15). The Star Citizen Wiki API and UEX clients, the ship data (concept ships from UEX) and name matching, the per-patch SQLite copy built on the player's PC, the ship look-up tool, the trade look-ups (UEX prices and routes), the player's fleet (`update_fleet`, `fleet.toml`), items and crafting (live from the Wiki API), and web search (a separate Claude request with Anthropic's search, on Star Citizen sites only).
- `src/tycho/brain/`: the brain (ADR 14). What it can decide and the checks on it, its briefing, and the Claude adapter. Its output is untrusted: it becomes commands that the safety gate checks.
- `src/tycho/voice/`: Tycho's voice (ADR 14). The ElevenLabs speaker, and playing speech as it streams in.
- `src/tycho/runtime/`: the talking loop (ADR 11). The session that joins every stage, the messages and per-stage timings, the transcriber, feedback and action-log boundaries, the action log file, and the single-instance lock.
- `src/tycho/safety.py`: the safety gate. Every press goes through `safety.check`, which allows, refuses or asks (ADR 13); the confirm-grade list lives here.
- `src/tycho/app.py`: the command line (`run`, `check`, `bind`, `sounds`, `gpu`, `key`, `credits`) and the wiring that joins the parts. `src/tycho/feedback.py`: the done, not-done and question sounds. `src/tycho/logs.py`: logging and key redaction. `src/tycho/text.py`: text normalising and similarity.
- `src/tycho/settings.py` and `src/tycho/paths.py`: the settings schema, and the only place file locations are decided. Each setting's schema entry says whether it can change by voice (`yes`, `lower_only` or `no`) and when a change applies.
- `src/tycho/tile/`: the Tile, the panel over the game (ADR 16). The design system as data (`design`), the card kit Claude fills with `show_card` (`cards`: the text is untrusted, cleaned and cut there), the rules for what shows and for how long (`board`), the drawing (`draw`: Pillow, checked as pixels), the see-through Win32 window on its own thread (`window`), and `display`, which joins them behind `runtime.Display` (a fake in `tests/fakes`). `tools/spike/tile/preview.py` draws every card to PNGs.
- `src/tycho/desktop/`: Tycho on the desktop (ADR 17). The tray icon (Win32, its own thread; pystray is LGPL), and `Host`, which runs the voice loop on its own thread so the main thread is free for the app window.
- `src/tycho/address.py`: what Tycho calls the player (#15): the brain's header line, the name at the end of a confirm question, and the speaker wrapper that says it as asked.
- `src/tycho/spoken_settings.py`: settings by voice (#111). The brain's `settings` tool (get, set, undo), reading what was said, and the voice rules. A new setting needs a rule; a test checks.
- `src/tycho/eval/`: scoring for benchmarks.
- `src/tycho/credentials.py`: API keys.
- `plugins/tycho/` and `.claude-plugin/marketplace.json`: the skills players' agents use to set Tycho up (`tycho-setup`) and write their own commands (`tycho-commands`), shipped as a Claude Code plugin; the README links them for other agents. `tests/test_agent_skills.py` keeps them true to the command line and file formats: change them with the CLI.
- `tools/bench/`: benchmarks and the brain eval (see its README). `tools/spike/`: the recorder, phrase lists, expected results and brain cases, the recommended-keybinds profile builder (`make_layout.py`), the in-game press tool (`press.py`), the self-test (`selftest.py`), and the Tile's window test (`tile/`).

The project folder may still be named `ultimo` locally; the project is Tycho ([ADR 7](docs/adr/0007-name-tycho.md)).

## Skills for agents working here

- `add-voice-command`: a new built-in command or sequence, with its tests and eval cases.
- `release`: bump the version, take CI's installer, draft the GitHub release, and publish when the maintainer agrees.

They're in `.claude/skills/` (Claude Code) and `.agents/skills/` (Codex and others), as identical copies: edit one, copy it to the other (`tests/test_agent_skills.py` checks).

## Conventions

- Python 3.14, `src/` layout, `asyncio`. Never block the event loop: use `asyncio.to_thread` for blocking calls.
- Every hardware boundary (audio, input, capture, joystick) and every outside service (speech-to-text, LLM, text-to-speech) is a `typing.Protocol`, with a fake in `tests/fakes/`. New boundaries must follow this; `ptt.InputSource` and `audio.AudioSource` are the patterns to copy.
- Code under `tools/` measures things and may call paid APIs; product code under `src/` must not depend on `tools/`.
- Key sequences must be cancellable and must release every held key in a `finally` block.
- Commits follow Conventional Commits: `feat(input): ...`, `fix(stt): ...`.
- Every change goes through a pull request: branch from `main`, push, open a PR, and merge only when CI and the Claude review pass. `main` is protected; never push to it directly.
- Pin every GitHub Action to a full commit SHA, with the version in a comment. The repo refuses tag refs like `@v4`.
- Significant decisions get an ADR in `docs/adr/`, using the next free number.
