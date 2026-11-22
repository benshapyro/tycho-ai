# Contributing to Tycho

Thanks for your interest. Tycho is pre-alpha, so expect things to move.

This page is for people. It has everything a pull request needs. [AGENTS.md](AGENTS.md) is written for AI coding agents: it repeats these rules and adds a map of the code, which is worth reading once you know what you want to change. If the two disagree, tell us: that's a bug.

## Your first contribution

Good places to start, smallest first:

- **A doc fix or a clearer message.** Anything a player reads follows [docs/voice-and-tone.md](docs/voice-and-tone.md): plain words, no blame, say what to do next.
- **A new way to say a built-in command,** or a new command. The steps are in the [add-voice-command guide](.claude/skills/add-voice-command/SKILL.md) (written as an agent skill, but it works as a checklist for a person).
- **A small, already-described fix.** Issues labelled [`from-review`](https://github.com/benshapyro/tycho-ai/issues?q=is%3Aopen+label%3Afrom-review) came out of code reviews: each says what's wrong and where. [`bug`](https://github.com/benshapyro/tycho-ai/issues?q=is%3Aopen+label%3Abug) issues are the other list.
- **Something bigger?** The roadmap is issue [#200](https://github.com/benshapyro/tycho-ai/issues/200). Comment on the issue before you start, so two people don't build the same thing, and so the maintainer can say whether it fits.

**What you need, and what you don't:**

- **A Windows PC.** Tycho is Windows-only (the game is), `uv sync` refuses other platforms, and CI runs on Windows. Linux, macOS and WSL aren't supported for development.
- **No game, no API keys and no recordings** to run the tests: every microphone, key press and outside service has a fake in `tests/fakes/`. The tests that drive Tycho's window use the PC's own Edge, and skip if it isn't there.
- **The game** only to look up real action names (`tycho actions <words>`) or to try a change in play. Without it, `tools/spike/recommended_keybinds.toml` lists action names you can read.
- **The paid and at-the-PC checks** (the brain eval, the recorded-speech replay, the self-test) are run by the maintainer before a release, not by you. If your change touches the brain's briefing or a command's phrases, say so in the pull request and the maintainer will run the eval.

## Setup

1. Install [uv](https://docs.astral.sh/uv/), version 0.13.9 or newer (CI uses 0.13.9; an older uv can rewrite `uv.lock`). It fetches Python 3.14 for you.
2. `uv sync --no-group gpu` creates the virtual environment (CI does the same). Plain `uv sync` adds about 2 GB of CUDA libraries, which you only need to run speech recognition on an NVIDIA card.
3. `uv run pre-commit install` sets up the commit hooks.

## Before opening a pull request

```sh
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
```

CI runs the same checks on Windows, plus the large-file and private-key checks.

Every change goes through a pull request: fork the repository, branch from `main`, push, and open a PR. `main` is protected; the maintainer merges a PR once CI passes and its review has no open blocking finding. An automated Claude review reads each PR, but it can't run on a PR from a fork (GitHub keeps the repository's secrets from fork runs): the maintainer reviews those by hand, or opens a copy of the branch here to get the automated review. Only blocking findings (a player's normal path, safety, security, keys) need fixing in your PR; the rest become issues.

## What a good pull request has

- **A test for new behaviour, and at least one test that fails without your change.**
- **A changelog line** if players will notice it (below).
- **One thing.** A fix and an unrelated tidy-up are two pull requests.
- **A note of what you couldn't run** (the eval, a check in the game), so the maintainer knows to.

These checks fail a pull request, and are easy to trip over:

- **No module in `src/tycho` over 500 lines,** and Star Citizen's code stays in `src/tycho/games/star_citizen/` (`tests/test_code_layout.py`, [ADR 19](docs/adr/0019-code-layout.md)).
- **No game's words in game-neutral code:** no "Star Citizen", "ship" or "quantum" outside a game's own folders (`tests/test_wording.py`).
- **Star Citizen's briefing is pinned by a hash** (`tests/test_packs.py`). If you change its text on purpose, update the hash, and say so: it needs an eval run.
- **The agent skills exist twice,** in `.claude/skills/` and `.agents/skills/`, as identical copies. Edit one and copy it to the other (`tests/test_agent_skills.py`).
- **Test coverage** can't drop below the floor in `pyproject.toml`.
- **A GitHub Action** must be pinned to a full commit SHA, with the version in a comment.

If players will notice your change, add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md), in plain words, with the PR or issue.

CI also measures test coverage of `src/tycho` (`uv run pytest --cov`) and fails below the floor in `pyproject.toml`. Raise the floor when your tests lift it; never lower it to get a PR through.

## Tests CI can't run

These adapters talk to Windows or the hardware directly, so only the self-test or a person checks them, and coverage counts them as missed: `input/windows.py` (SendInput and the focus check; the self-test), `ptt/windows.py` (button polling; the self-test), `ptt/joystick.py` (SDL joysticks), `audio/microphone.py` (the microphone), `feedback.py`'s `SoundDevicePlayer` and `voice/playback.py` (the speakers), `tile/window.py` (the Tile's window; `tools/spike/tile/`), and `desktop/tray.py` and `desktop/window.py` (the tray icon and the app window). Keep them thin: logic belongs in the tested code they call.

Before a release, the maintainer runs these on a Windows PC with the game installed and nobody using it:
- `uv run pytest -m replay -s`: the recorded test set, through the real speech model.
- `uv run python tools/spike/selftest.py`: the whole loop through real Windows input. It takes the foreground, so don't touch the PC while it runs.
- `uv run python tools/bench/tycho_eval.py`: the brain eval (about 5 cents, with your Anthropic key).

## Ground rules

- **Clean room.** Don't copy or port code from GPL-licensed projects, including Wingman AI. Tycho is Apache-2.0. Reading their docs and learning from their ideas is fine.
- **Respect CIG's rules.** No reading game memory, no injection, no unattended automation. Every action must start from the user and have a fixed end.
- **Every hardware boundary sits behind an interface,** so it can be tested with fakes.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat(input): add scancode sender`.
- **Big decisions** get a short record in `docs/adr/` (see the [index](docs/adr/README.md)). "Big" means hard to reverse, or a change to a promise players rely on: what leaves their PC, what the safety gate holds, a licence. Raise it in an issue first; decision records are the maintainer's call.
- **Recordings are personal.** Your voice clips and benchmark results live in `recordings/`, which git ignores. Audio is only committed under `tests/fixtures/`, on purpose, with the speaker's consent.
- **Benchmarks can cost money.** Tools in `tools/bench/` that call paid APIs use your own keys and print their cost; see [its README](tools/bench/README.md).

## Licence

By contributing, you agree that your contributions are licensed under Apache-2.0.
