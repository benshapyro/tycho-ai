# Contributing to Tycho

Thanks for your interest. Tycho is pre-alpha, so expect things to move.

## Setup

Tycho is Windows-only (the game is), and `uv sync` refuses other platforms.

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync` creates the virtual environment, with Python 3.14. It includes about 2 GB of CUDA libraries for NVIDIA cards; use `uv sync --no-group gpu` without one (CI does).
3. `uv run pre-commit install` sets up the commit hooks.

## Before opening a pull request

```sh
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
```

CI runs the same checks on Windows, plus the large-file and private-key checks.

Every change goes through a pull request: branch from `main`, push, and open a PR. `main` is protected; a PR merges once CI and the automated Claude review pass. Pin any GitHub Action to a full commit SHA, with the version in a comment.

## Tests CI can't run

Before a release, on a Windows PC with the game installed and nobody using it:
- `uv run pytest -m replay -s`: the recorded test set, through the real speech model.
- `uv run python tools/spike/selftest.py`: the whole loop through real Windows input. It takes the foreground, so don't touch the PC while it runs.
- `uv run python tools/bench/tycho_eval.py`: the brain eval (about 2 cents, with your Anthropic key).

## Ground rules

- **Clean room.** Don't copy or port code from GPL-licensed projects, including Wingman AI. Tycho is Apache-2.0. Reading their docs and learning from their ideas is fine.
- **Respect CIG's rules.** No reading game memory, no injection, no unattended automation. Every action must start from the user and have a fixed end.
- **Every hardware boundary sits behind an interface,** so it can be tested with fakes.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat(input): add scancode sender`.
- **Big decisions** get a short record in `docs/adr/` (see the [index](docs/adr/README.md)).
- **Recordings are personal.** Your voice clips and benchmark results live in `recordings/`, which git ignores. Audio is only committed under `tests/fixtures/`, on purpose, with the speaker's consent.
- **Benchmarks can cost money.** Tools in `tools/bench/` that call paid APIs use your own keys and print their cost; see [its README](tools/bench/README.md).

## Licence

By contributing, you agree that your contributions are licensed under Apache-2.0.
