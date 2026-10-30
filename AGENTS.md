# AGENTS.md

Instructions for AI coding agents working on Tycho, an unofficial voice co-pilot for Star Citizen. Read [docs/architecture.md](docs/architecture.md) for what we're building, and [docs/adr](docs/adr) for why.

## Commands

- Install: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check`
- Format: `uv run ruff format`
- Type check: `uv run pyright` (strict mode)

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
- `src/tycho/game/`: the game install, `Data.p4k` and CryXmlB readers, keybinds, and the keybind store.
- `src/tycho/lexicon/`: the game-term corrector and its term list.
- `src/tycho/commands/`: the fast-path command matcher and its command list.
- `src/tycho/eval/`: scoring for benchmarks.
- `src/tycho/credentials.py`: API keys.
- `tools/bench/`: benchmarks (see its README). `tools/spike/`: the recorder, phrase lists and expected results.

The project folder may still be named `ultimo` locally; the project is Tycho ([ADR 7](docs/adr/0007-name-tycho.md)).

## Conventions

- Python 3.14, `src/` layout, `asyncio`. Never block the event loop: use `asyncio.to_thread` for blocking calls.
- Every hardware boundary (audio, input, capture, joystick) and every outside service (speech-to-text, LLM, text-to-speech) is a `typing.Protocol`, with a fake in `tests/fakes/`. New boundaries must follow this; `ptt.InputSource` and `audio.AudioSource` are the patterns to copy.
- Code under `tools/` measures things and may call paid APIs; product code under `src/` must not depend on `tools/`.
- Key sequences must be cancellable and must release every held key in a `finally` block.
- Commits follow Conventional Commits: `feat(input): ...`, `fix(stt): ...`.
- Significant decisions get an ADR in `docs/adr/`, using the next free number.
