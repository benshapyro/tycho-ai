# Contributing to Ultimo

Thanks for your interest. Ultimo is pre-alpha, so expect things to move.

## Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. `uv sync` creates the virtual environment, with Python 3.14.
3. `uv run pre-commit install` sets up the commit hooks.

## Before opening a pull request

```sh
uv run ruff format
uv run ruff check
uv run pyright
uv run pytest
```

CI runs the same checks on Windows.

## Ground rules

- **Clean room.** Don't copy or port code from GPL-licensed projects, including Wingman AI. Ultimo is Apache-2.0. Reading their docs and learning from their ideas is fine.
- **Respect CIG's rules.** No reading game memory, no injection, no unattended automation. Every action must start from the user and have a fixed end.
- **Every hardware boundary sits behind an interface,** so it can be tested with fakes.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat(input): add scancode sender`.
- **Big decisions** get a short record in `docs/adr/`.

## Licence

By contributing, you agree that your contributions are licensed under Apache-2.0.
