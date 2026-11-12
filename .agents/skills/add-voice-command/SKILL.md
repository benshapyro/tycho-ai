---
name: add-voice-command
description: Add or change a built-in Tycho voice command or sequence in src/tycho/packs/star-citizen/commands.toml, with its tests and eval cases. Use when working in the Tycho repository and asked to support a new phrase, a new game action, or a new multi-step sequence for every player. A single player's own phrases go in their %APPDATA%\tycho\commands.toml instead, which the tycho-commands skill covers.
---

# Add a built-in voice command

Built-in commands are Tycho's fast path: phrases matched on the player's PC, without the language model. The rules that matter most, from the matcher (`src/tycho/commands/matcher.py`):

1. A wrong action is worse than a slow one. A phrase that could mean two things isn't a fast-path phrase; leave it to the brain.
2. A near match never flips meaning: "lights on" can never match "lights off".
3. Every press goes through the safety gate (`src/tycho/safety.py`). Never weaken it, and never add to `_NOT_DANGER` to get a command through.

## 1. Find the action

- **Look up the name:** `uv run tycho actions <words>` lists the game's actions with their labels, keys and action maps, from the game installed on this PC. Tycho's own commands said with those words are listed too.
- **Prefer the specific action** ("Open All Doors"). A toggle goes after it, as the fallback: pressed blind, a toggle can do the opposite of what was asked.
- **Unbound by default:** if the action has no key in the default profile, Tycho can't press it until the player binds one. Say so in the PR. `tools/spike/make_layout.py` gives specific on and off actions keys in a profile of their own.
- **Confirm-grade actions** (ejecting, self-destruct, jettison, firing, respawn: `CONFIRM_GRADE` and `_DANGER_WORDS` in `safety.py`) always ask "confirm". Give them `confirm = true` too, so the file says so.

## 2. Write the entry

In `src/tycho/packs/star-citizen/commands.toml`, next to its neighbours (the file is grouped by system):

```toml
[[command]]
id = "lights_on"
say = ["lights on", "turn the lights on", "turn on the lights"]
actions = ["v_lights_on", "v_lights"]
```

- **`say`:** lower case, the ways players really say it. Add the mishearings speech-to-text makes ("hang your request" for "hangar request"): an exact phrase is instant and certain. Leave out fillers ("please", "can you"); the matcher strips them.
- **`announced = true`:** when the ship's computer or air traffic control says it's done, so Tycho stays quiet.
- **`setup`, `settle_seconds`, `requires`:** see the `Command` docstrings in `matcher.py`. `requires` may only name facts that `safety.known_facts()` lists.
- **A sequence** is `[[sequence]]` with `steps`, at the end of the file. Each step is a command id, or `{ command = "...", wait = 1.0 }`.

## 3. Test it

- **Unit tests,** in `tests/test_commands.py`:
  - add the new wordings to `test_matches_unseen_wordings`, using wordings that aren't in `say`;
  - add anything that must not match it to `test_never_flips_meaning` or `test_opposites_and_neighbours_never_cross`;
  - add negations to `test_negations_always_defer`.

  `test_no_two_commands_are_confusable` and `test_every_command_action_name_is_plausible` run on every entry by themselves.
- **The fast-path eval:** add each new phrase to `tools/spike/phrases.txt`, and its expected result to `tools/spike/expected.toml`. Write the expected result before running anything.
- **The brain eval:** if players will say it loosely, add a case or two to `tools/spike/brain_cases.toml` (`heard`, `mode = "command"`, `expect = ["actions"]`, `must`, `never`). Every case needs a `mode` (ADR 10); the eval refuses the file without one.
  - `uv run python tools/bench/tycho_eval.py --tag <tag>` runs them.
  - It calls the Anthropic API and costs money, so ask before running it.
- **Done means:** `uv run ruff format --check`, `uv run ruff check`, `uv run pyright` and `uv run pytest` all pass.

## 4. Ship it

Follow `AGENTS.md`:
- branch from `main`;
- commit as `feat(commands): ...`;
- open a PR, and merge only when CI and the Claude review pass, and the maintainer says so.

In the PR, say:
- which phrases you added;
- which actions they press, with the fallbacks;
- whether those actions have a key by default;
- what the evals showed.
