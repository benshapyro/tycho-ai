# AGENTS.md

Instructions for AI coding agents working on Tycho, an unofficial voice co-pilot for Star Citizen. Read [docs/architecture.md](docs/architecture.md) for what we're building, and [docs/adr](docs/adr) for why.

This is the one instructions file for every coding agent: Claude Code reads it directly (there's no CLAUDE.md), as do Codex, Cursor and others. Some folders have their own AGENTS.md with the rules for that part, read when you work there: `src/tycho/brain/`, `src/tycho/knowledge/`, `src/tycho/desktop/` and `tools/bench/`.

**What to work on next:** the pinned roadmap issue, [#200](https://github.com/benshapyro/tycho-ai/issues/200) (`gh issue view 200`), lists the order of work by milestone. Read it before picking up an issue, and tick its line when you close one.

## Commands

- Install: `uv sync`
- Test: `uv run pytest`
- Build the installed app: `uv sync --group build`, then `uv run pyinstaller packaging/tycho.spec --noconfirm` (dist/tycho/tycho.exe for the terminal, and tycho-app.exe, the Start menu's Tycho, with no terminal), then `uv run python tools/notices.py dist/tycho` for THIRD-PARTY-NOTICES.txt (it fails on any GPL, LGPL or non-commercial licence). The installer is built in CI (`.github/workflows/build.yml`) with Inno Setup from `packaging/tycho.iss`. The build must never include CUDA libraries, pygame or pysdl2-dll's DLLs other than SDL2.dll: CI fails if it does.
- Lint: `uv run ruff check`
- Format: `uv run ruff format`
- Type check: `uv run pyright` (strict mode)
- Replay test (needs the speech model and local recordings; not in CI): `uv run pytest -m replay -s`
- End-to-end self-test through real Windows input, without the game (only when nobody is using the PC): `uv run python tools/spike/selftest.py`

Use `uv`. Never use `pip` or a bare `python`.

## Done means

All four of these pass: `ruff format --check`, `ruff check`, `pyright`, and `pytest`. New behaviour has tests.

A change a player can notice also adds a line under **Unreleased** in `CHANGELOG.md`, in plain words, with its PR or issue. Anything that breaks an existing setup (a file that stops loading, a setting that changes meaning) says so first, with how to fix it.

## Hard rules

- Never copy or port code from GPL-licensed projects, including Wingman AI. Tycho is Apache-2.0.
- Never read or write game memory, inject into the game process, or hook DirectX.
- Every action must go through the safety gate, be started by the user and have a fixed end. No loops or timers acting alone.
- Never write API keys to files or logs. Use `tycho.credentials` (src/tycho/credentials.py), which stores them in Windows Credential Manager; add new provider names to `PRODUCT` (keys Tycho uses, offered by `tycho key`) or `BENCH` (keys only `tools/bench` uses).
- The safety gate is independent of every model: no router or brain output may skip a confirm-grade action's confirmation (architecture 4.7, ADR 10).
- Any local server binds to `127.0.0.1` and requires a token.

## Where things are

- `src/tycho/ptt/`: push-to-talk on keyboard, mouse and joystick buttons.
- `src/tycho/audio/`: microphone capture and push-to-talk clips.
- `src/tycho/game/`: the game install, `Data.p4k` and CryXmlB readers, keybinds, the keybind store, profile building, and the `Game.log` reader.
- `src/tycho/packs/`: game packs (ADR 18). A pack is a folder of data Tycho validates and never runs: `pack.toml` (id, version, format, mode and why, the built-in parts it names by id, and `call_me`, what to call the player there: #349), the game part of the brain's briefing (`briefing.md`, which places the core sections from `brain/core.md` once each), `facts.md`, `terms.txt`, `commands.toml`, `tile.toml` and `safety.toml`. `pack` loads and checks one; `parts` is the registry of built-in parts (install, keybinds, game state, look-ups). Star Citizen is `packs/star-citizen/`; its code stays in `game/` and `knowledge/`. World of Warcraft: Forever is `packs/wow-forever/`, an advisor pack (#300; Forever, not modern WoW or Classic) written in Tycho's own words with no look-ups: read a source's terms, and ask the maintainer, before adding one. A fake second game and a fake advisor game are in `tests/packs/`. Star Citizen's briefing is pinned byte for byte (`tests/test_packs.py`): if you change its text on purpose, update the hash there.
- `src/tycho/lexicon/`: the game-term corrector (the words are the pack's `terms.txt`).
- `src/tycho/commands/`: the fast-path command matcher, the pack's command list and sequences, and merging in the player's own `commands.toml` (ADR 14).
- `src/tycho/input/`: the hands. Key codes, press planning from the game's activation modes, the presser (focus-guarded, cancellable, always releases), and the Windows sender.
- `src/tycho/stt/`: speech to text. Pinned, checksummed model downloads (`ModelStore`), choosing the GPU or CPU, and the Parakeet transcriber.
- `src/tycho/knowledge/`: look-ups (ADR 15). The Star Citizen Wiki API and UEX clients, the ship data (concept ships from UEX) and name matching, the per-patch SQLite copy built on the player's PC, the ship look-up tool, the trade look-ups (UEX prices and routes), the player's fleet (`update_fleet`, `fleet.toml`), items and crafting (live from the Wiki API), and web search (a separate Claude request with Anthropic's search: Star Citizen sites first, then the open web when they have no clear answer).
- `src/tycho/brain/`: the brain (ADR 14). What it can decide and the checks on it, its briefing, the Claude adapter, the OpenRouter adapter (players' `brain_provider = "openrouter"`, and the eval's way to measure other models, #192), and `providers`, the allowlist of models a player can pick (#112: only models that passed the eval with zero wrong presses; no free-text ids). Its output is untrusted: it becomes commands that the safety gate checks.
- `src/tycho/voice/`: Tycho's voice (ADR 9, ADR 14). The ElevenLabs speaker, playing speech as it streams in, and `voices`: the shortlist, each voice's model, and the free check at start that the player's key can use it (#242).
- `src/tycho/runtime/`: the talking loop (ADR 11). The session that joins every stage (threads, cancellation, barge-in), and the parts it calls (#55): `routing` (where an utterance goes) and `planning` (the presses and the safety gate's verdict), both pure so the brain eval calls them too, the `reporter` (log and action log) and the `Conversation` (follow-ups, the last line); the messages and per-stage timings, the transcriber, feedback and action-log boundaries, the action log file, and the single-instance lock.
- `src/tycho/safety.py`: the safety gate. Every press goes through `safety.check`, which allows, refuses or asks (ADR 13). In advisor mode (#191: the pack's `mode = "advisor"`, with its `why`, or the player's `key_actions` off, joined by `packs.as_run`) the lists' `keys_off` is set, and the gate's first check refuses every press; the brain then gets no `run_actions` or `run_sequence`, and `brain/advisor.md` takes the place of the core's tool rules. The rules and the danger-word backstop live here; the lists (confirm-grade actions, travel, the mode and state guards) are the game pack's `safety.toml`, and only a pack that ships inside Tycho may set a list that loosens a check (ADR 18).
- `src/tycho/app.py`: the command line (`run`, `check`, `bind`, `sounds`, `gpu`, `key`, `credits`) and the wiring that joins the parts. `src/tycho/brain_choice.py`: which brain runs (the settings' provider and model, its key, its look-ups) and what `tycho check` says about it. `src/tycho/findings.py`: `Finding`, what a check found, and reading the player's stored keys. `src/tycho/feedback.py`: the done, not-done and question sounds. `src/tycho/logs.py`: logging and key redaction. `src/tycho/text.py`: text normalising and similarity.
- `src/tycho/settings.py` and `src/tycho/paths.py`: the settings schema, and the only place file locations are decided. Each setting's schema entry says whether it can change by voice (`yes`, `lower_only` or `no`) and when a change applies.
- `src/tycho/tile/`: the Tile, the panel over the game (ADR 16). The design system as data (`design`), the card kit Claude fills with `show_card` (`cards`: the text is untrusted, cleaned and cut there), the rules for what shows and for how long (`board`), the drawing (`draw`: Pillow, checked as pixels), the see-through Win32 window on its own thread (`window`), and `display`, which joins them behind `runtime.Display` (a fake in `tests/fakes`). `tools/spike/tile/preview.py` draws every card to PNGs.
- `src/tycho/desktop/`: Tycho on the desktop (ADR 17). The tray icon (Win32, its own thread; pystray is LGPL), the tray menu (`menu`, tested without a taskbar), the app window (`window`: pywebview, the page loaded from memory, no server), its page (`app/`: HTML, CSS and JS, all drawn from the bridge's data), `setup` (what setup needs from the PC: the game, a button press, the microphones and their level, each a stand-in in tests), `commands` (the Commands page: the list, and the player's own phrases written to their commands.toml with tomlkit, keeping their comments, checked by loading it before it's saved), `bridge`, the page's only way into Python (picking a game in Settings restarts the voice loop through `Host.restart`, #295; the Commands page is the chosen game's) (it checks everything it's given; keys go in and never come out), and `Host`, which runs the voice loop on its own thread so the main thread is free for the app window. `src/tycho/win32.py`: the Win32 structures and DLL types the Tile window and the tray share.
- `src/tycho/address.py`: what Tycho calls the player (#15): the brain's header line, the name at the end of a confirm question, and the speaker wrapper that says it as asked.
- `src/tycho/spoken_settings.py`: settings by voice (#111). The brain's `settings` tool (get, set, undo), reading what was said, and the voice rules. A new setting needs a rule; a test checks.
- `src/tycho/eval/`: scoring for benchmarks.
- `src/tycho/credentials.py`: API keys.
- `plugins/tycho/` and `.claude-plugin/marketplace.json`: the skills players' agents use to set Tycho up (`tycho-setup`) and write their own commands (`tycho-commands`), shipped as a Claude Code plugin; the README links them for other agents. `tests/test_agent_skills.py` keeps them true to the command line and file formats: change them with the CLI.
- `tools/bench/`: benchmarks and the brain eval (see its README). `tools/spike/`: the recorder, phrase lists, expected results and brain cases, the recommended-keybinds profile builder (`make_layout.py`), the in-game press tool (`press.py`), the self-test (`selftest.py`), and the Tile's window test (`tile/`).

The project folder may still be named `ultimo` locally; the project is Tycho ([ADR 7](docs/adr/0007-name-tycho.md)).

## Brain traces: what the brain did, and why

Start here when asked why Tycho pressed, answered or failed something (#282). Each brain request can write one JSON line: the words, each call to the model (its tool calls with their arguments, time to the first token, tokens and cost), each look-up (time and size, not its result), what it decided, and why it stopped. The briefing is a hash; keys and absolute paths never appear.

- **Play:** off for players. Run with `TYCHO_TRACE=1` and the lines go to `traces.jsonl` in the logs folder (`%LOCALAPPDATA%\tycho\logs`), one `run` per session. The lines keep what the player said, Tycho's replies and its web searches, even with `keep_transcripts` off (#306): never ask a player to turn tracing on without telling them that first.
- **The eval** always writes them, each with its `case` (the words heard), to `recordings/tycho_eval.traces.jsonl`; the results file's `meta` line names the run.
- **Read them** with `uv run python tools/bench/traces.py`: `runs` lists the runs in a file, `summary` gives a run's outcomes, times, look-ups, errors, cost (and where each call's cost came from: reported, the price list, Tycho's estimate, or looked up by the eval after the run, #317) and slowest requests (with `--results recordings/tycho_eval.results.jsonl`, its wrong presses too), and `diff` compares two runs case by case. For one request, `jq` or `grep` the line.
- **Where it's written:** `src/tycho/brain/traces.py` (the line), recorded through `brain/rounds.py`, so both brains write the same lines.

## Skills for agents working here

- `add-voice-command`: a new built-in command or sequence, with its tests and eval cases.
- `release`: bump the version, take CI's installer, draft the GitHub release, and publish when the maintainer agrees.

They're in `.claude/skills/` (Claude Code) and `.agents/skills/` (Codex and others), as identical copies: edit one, copy it to the other (`tests/test_agent_skills.py` checks).

## Reviews

- The Claude review (`.github/workflows/claude-code-review.yml`) runs on Opus when a PR is opened or marked ready, and again when the `claude-review` label is added. It reads the whole PR and checks each finding. Its summary lists what it read and anything it couldn't do: a review with a "Couldn't:" line, or a "denied tools" warning on the run, is incomplete, not clean.
- **Fix in the PR only what's blocking:** it breaks a player's normal path, a safety rule, security or keys. Everything else becomes a GitHub issue labelled `from-review`, cleared before the next release. Nothing reaches players before a release.
- **One push for all of a review's fixes**, then add `claude-review` only if the fixes were substantial. Each review costs about $1.50-2; reviewing every push cost $10+ on one PR.
- Unresolved review threads block the merge. The review resolves its own threads once the code fixes them; otherwise the maintainer resolves them.
- For a large change, also run an independent review before merging.

## Conventions

- Python 3.14, `src/` layout, `asyncio`. Never block the event loop: use `asyncio.to_thread` for blocking calls.
- Every hardware boundary (audio, input, capture, joystick) and every outside service (speech-to-text, LLM, text-to-speech) is a `typing.Protocol`, with a fake in `tests/fakes/`. New boundaries must follow this; `ptt.InputSource` and `audio.AudioSource` are the patterns to copy.
- Code under `tools/` measures things and may call paid APIs; product code under `src/` must not depend on `tools/`.
- Key sequences must be cancellable and must release every held key in a `finally` block.
- Commits follow Conventional Commits: `feat(input): ...`, `fix(stt): ...`.
- Every change goes through a pull request: branch from `main`, push, open a PR, and merge only when CI and the Claude review pass. `main` is protected; never push to it directly.
- Pin every GitHub Action to a full commit SHA, with the version in a comment. The repo refuses tag refs like `@v4`.
- Significant decisions get an ADR in `docs/adr/`, using the next free number.
