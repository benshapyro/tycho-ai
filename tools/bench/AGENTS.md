# Benchmarks and the brain eval

Code here measures things and may call paid APIs. Product code under `src/` must never import from `tools/`. Read the root AGENTS.md first; README.md here explains each tool.

## The brain eval (`tycho_eval.py`)

- **It runs every case in `tools/spike/brain_cases.toml` through Tycho's real path:** the lexicon, the fast path, then the brain with the player's keybinds and live look-ups. It presses nothing.
- **Paid:** about 10 cents a run. Ask the maintainer before running it, and give the measured cost.
- **It grades:**
  - the answer: wrong presses, what must be pressed, `mentions`, cards;
  - and how it was reached: `max_calls`, `tools`, `no_tools`, `no_errors`.
- **The bar is zero wrong presses.**
- **Add a case for every miss a play log shows,** tagged and commented with where it came from. A case's keys must be ones the eval reads (`tests/test_tycho_eval.py` checks), and every case needs a `mode` from `brain_report.MODES` (ADR 10, #24): what the player wants, not the right response.
- **Running it:**
  - Run it from a git worktree of the branch under test, never the folder the maintainer runs Tycho from.
  - It needs the game's keybinds and the Anthropic key from Credential Manager. Off the PC (a cloud thread, #262) it reads `--keybinds <file>` from `--export-keybinds` instead, and `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` from the environment. Only the eval reads keys from the environment; nothing in `src/` may.
  - The PC may stop it when memory is low, for example while the game runs. Don't restart it without asking.
- **Results** are appended to `recordings/tycho_eval.results.jsonl`. Compare with the last full run, not a tagged subset.
