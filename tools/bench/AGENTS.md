# Benchmarks and the brain eval

Code here measures things and may call paid APIs. Product code under `src/` must never import from `tools/`. Read the root AGENTS.md first; README.md here explains each tool.

## The brain eval (`tycho_eval.py`)

- **Each game pack has its own cases** (ADR 18): `src/tycho/packs/<id>/eval/cases.toml`, run with `--pack <id>` (Star Citizen by default). The results' `meta` line and the trace run name the pack, its version and its briefing's hash. A pack with key actions must meet the same bar: zero wrong presses.
- **It runs every case in `src/tycho/packs/star-citizen/eval/cases.toml` through Tycho's real path:** the lexicon, the fast path, then the brain with the player's keybinds and live look-ups. Routing and planning (with the safety gate) are the session's own `route` and `plan_request` (#55): never copy them into the eval. It presses nothing.
- **Paid:** about 5 cents a run (measured 11 November 2026; more with web search or a larger model). Ask the maintainer before running it, and give the measured cost.
- **It grades:**
  - the answer: wrong presses, what must be pressed, `mentions`, cards;
  - the wording (`docs/voice-and-tone.md`, #363): the file's `never_say` (another game's words, in any reply: every pack's cases name the other packs' games, and `tests/test_wording.py` checks), the file's `say_words` (the most words in the line said with a press) a case's `max_words` (the most words in its reply) and a case's `never_say` (words its line must not say, such as a toggle's direction, #545);
  - A case's `unbound` takes the key off those actions for that case only, to grade what Tycho does when the player hasn't bound one (#545). A name the keybinds don't have stops the run.
  - and how it was reached: `max_calls`, `tools`, `no_tools`, `no_errors`.
  - It also counts replies that end in a question and take more than one sentence (#430): a clarifying question is one sentence, but a reply may answer and then offer the rest, so it's a measure, not a verdict.
  - Only this eval grades wording. `brain_report.py` and `jev_report.py` read the same `cases.toml` with their own grader, which ignores `never_say`, `say_words` and `max_words` (#430).
- **The bar is zero wrong presses.** A pick the case allows, which the gate refuses only because its key is also another confirm-grade action's on the keybinds in use, is graded `KEY CLASH`, apart from wrong presses and passes: it measures the keybinds, not the model (#494). Fix the keybinds, not the case. A built-in command the fast path takes, whose action has no key on those keybinds, presses nothing and picks nothing: it passes where the case accepts a reply, and is never a wrong press (#508).
- **Add a case for every miss a play log shows,** tagged and commented with where it came from. A case's keys must be ones the eval reads (`tests/test_tycho_eval.py` checks), and every case needs a `mode` from `brain_report.MODES` (ADR 10, #24): what the player wants, not the right response.
- **Running it:**
  - Run it from a git worktree of the branch under test, never the folder the maintainer runs Tycho from.
  - It needs the game's keybinds and the Anthropic key from Credential Manager. Off the PC (a cloud thread, #262) it reads `--keybinds <file>` from `--export-keybinds` instead, and `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` from the environment. Only the eval reads keys from the environment; nothing in `src/` may.
  - The PC may stop it when memory is low, for example while the game runs. Don't restart it without asking.
- **Results** are appended to `recordings/tycho_eval.results.jsonl`. Compare with the last full run, not a tagged subset.
- **Traces** of every brain request go to `recordings/tycho_eval.traces.jsonl`, with each case (#282). `traces.py summary --results recordings/tycho_eval.results.jsonl` gives the last run at a glance, and `traces.py diff --run OLD --run NEW` shows what a change did, case by case. It's free: it only reads files.
