# How Tycho was built

A short tour of the engineering behind Tycho, for anyone who wants to judge it rather than play it: what was decided and why, what was measured, what failed, and what the evidence can't show yet. Each point links to where it lives in the repository.

Tycho is built by one maintainer directing AI coding agents. The maintainer makes the product decisions; the agents write nearly all of the code, under written rules. [How the work is split](#how-the-work-is-split) says who does what.

## The five ideas that carry it

### 1. The model proposes; a gate outside the model decides

A language model picks what to do with anything the built-in commands don't match. Its output is treated as untrusted input.

- Every action the model names is checked against the keys the player has actually bound. A name that isn't there is answered, never pressed ([`brain/decisions.py`](../../src/tycho/brain/decisions.py), [`brain/AGENTS.md`](../../src/tycho/brain/AGENTS.md): "Never trust a pick").
- Every press, from any path, then goes through one safety gate ([`safety.py`](../../src/tycho/safety.py)). Anything that can't be undone waits for a spoken "confirm". A game pack can add to the gate's danger list and can never take away from it.
- The model can choose a multi-step sequence only by name. The steps come from a file, never from the model ([ADR 14](../adr/0014-the-brain-voice-and-sequences.md)).
- Nothing is pressed after a web search, because text from the web can't be checked (`NO_PRESS_AFTER_SEARCH` in [`brain/rounds.py`](../../src/tycho/brain/rounds.py)).
- By voice, a setting that protects the player can only get stricter. A misheard sentence must never weaken a safeguard ([ADR 5](../adr/0005-settings-one-schema-three-ways-in.md)).

The gate holds only what can't be undone. A wrong pick of an ordinary action (shields, weapon power) would still be pressed, so the briefing and the eval guard those. One exception is written down: quantum travel on a shared key, which sets a safe mode first and accepts a remaining risk ([ADR 12](../adr/0012-modes-before-shared-keys.md)).

### 2. Models are chosen by measurement, and the losers are written down

- The first benchmark picked Claude Haiku: 97% on the test, about 1.2 s and about $0.0002 a request. Larger Claude models matched it on a first sample at higher cost and latency. Two much faster models were rejected because they "pressed actions that must be confirmed (quantum, jump drive, missiles) without being asked" ([ADR 6](../adr/0006-cloud-brain-local-ears.md), Result).
- A fast routing model answered in about 0.12 s but made 3 confident wrong picks, and its danger checks were unreliable. It was kept out of the loop ([ADR 10](../adr/0010-interaction-modes-and-routing.md)).
- Nine models were run through the same eval in November. None beat Haiku on wrong presses ([#192](https://github.com/benshapyro/tycho-ai/issues/192)). The run also showed Haiku itself had one wrong press, which led to the briefing fixes in [#271](https://github.com/benshapyro/tycho-ai/issues/271).
- A player can only pick a model that scored zero wrong presses on the eval. The settings refuse any other ([`brain/providers.py`](../../src/tycho/brain/providers.py)).

### 3. The eval's bar is safety first, then speed

The brain eval runs every case through Tycho's real path (the word corrector, the built-in commands, the model, the safety gate) and presses nothing ([`tools/bench/README.md`](../../tools/bench/README.md), [`tools/bench/AGENTS.md`](../../tools/bench/AGENTS.md)).

- **The bar is zero wrong presses.** Speed comes second (the model's picks at a median of 1.5 s or less).
- It also fails an answer that only points elsewhere ("check the wiki"), a reply that narrates its own reasoning, and wording that breaks the [voice and tone guide](../voice-and-tone.md).
- It grades how an answer was reached: how many model calls, which look-ups, and whether any came back as errors.
- An adapter check runs the same model directly and through OpenRouter, so a gap points at Tycho's adapter and not at the model.
- Every request writes a trace line, and a diff tool compares two runs case by case.
- A case is added for every miss a play log shows.

### 4. The prompt is built around the cache and the clock

- The briefing (the system prompt, with the player's own bound actions) is cached for an hour and warmed up when Tycho starts. Tool order is treated as part of the cache ([ADR 14](../adr/0014-the-brain-voice-and-sequences.md)).
- A look-up that finds nothing returns an answer, not an error, because an error makes the model retry and each retry costs 1 to 1.5 seconds ([`brain/AGENTS.md`](../../src/tycho/brain/AGENTS.md)).
- One-tool-per-reply was reversed after a play session showed its cost, and the eval judged the change before it merged (ADR 14, update of 6 November).
- Each game pack must place every core section of the briefing exactly once, so a pack can't drop the safety rules. Star Citizen's briefing is pinned by hash in a test.
- Common commands never reach a model at all: they match on the PC in a few milliseconds.

### 5. Privacy is designed in, then tested

- Speech recognition runs on the player's PC. Only text leaves, and the README lists [every outside call](../../README.md#what-leaves-your-pc).
- Crash reports are opt-in. A report is built from an allowlist, a scrubber drops anything that doesn't fit, and tests plant private values to prove they don't get through ([ADR 4](../adr/0004-built-for-every-player.md), [`crash_reports.py`](../../src/tycho/crash_reports.py)).
- A review found that the OpenRouter route could be served by a host the README didn't name. The route was pinned to match the promise ([#313](https://github.com/benshapyro/tycho-ai/issues/313)).

## The numbers, with their dates

The last full run of the brain eval recorded in the repository's issues:

| | |
|---|---|
| Date | 11 November 2026 ([#192](https://github.com/benshapyro/tycho-ai/issues/192)) |
| Model | Claude Haiku 5.5, direct |
| Cases | 191 |
| Passed | 185 |
| Wrong presses | 0 |
| Errors | 0 |
| Median time to act | about 1.7 s (the bar is 1.5 s: not met on this run) |
| Cost | about $0.0003 a request |

In the maintainer's own play (31 October to 8 November), the model was asked a median of 3.4 times an hour, which comes to well under a cent an hour ([#192](https://github.com/benshapyro/tycho-ai/issues/192)).

Older numbers still quoted in the decision records come from smaller runs: 97% on 82 typed cases (30 October, [ADR 6](../adr/0006-cloud-brain-local-ears.md)), and 29 of 30 recorded phrases transcribed exactly ([ADR 2](../adr/0002-cascaded-voice-pipeline.md)). The Star Citizen eval has 195 cases today, and World of Warcraft: Forever has 30 of its own.

## Limits of the evidence

- **One voice, one PC.** Every recording and every timing comes from the maintainer's voice and machine.
- **No held-out cases.** The eval's cases are written by the same people and agents who write the briefing, and many come from the maintainer's own play. A good score shows the known misses are fixed, not that unknown ones are rare.
- **Runs vary.** Look-up and card cases pass and fail between identical runs. Single-case changes are noise until they repeat.
- **Results aren't in the repository.** Result files stay on the maintainer's PC with the recordings. The numbers above are copied from issue comments, so they can't be reproduced from a fresh clone without the maintainer's keybinds file and a paid run (about 5 cents).
- **The frame-rate cost of running beside the game is unmeasured.** Speech recognition moved to the processor in 0.8 after it caused graphics crashes on the card ([#188](https://github.com/benshapyro/tycho-ai/issues/188)).
- **Few players.** Tycho is pre-alpha and has been played mostly by its maintainer.
- **Not built yet:** screen vision, the full state tracker, and learning from misses. [The architecture page](../architecture.md) says which parts are planned.

## How the work is split

**The maintainer decides** what Tycho is and isn't: the product, the safety bar, which model, what leaves the PC, money, releases, and every decision record. Decisions made by the maintainer are marked as the author's in the [decision records](../adr/README.md).

**AI coding agents do the building**: the code, the tests, the pull requests and most of this documentation. They work under [`AGENTS.md`](../../AGENTS.md), which holds the hard rules (clean-room code, no game memory, every action through the safety gate, keys never in files) and a map of the code.

What keeps that honest:

- **Every change is a pull request** that must pass CI on Windows and an automated review by a second Claude session ([the workflow](../../.github/workflows/claude-code-review.yml)). Only blocking findings are fixed in the pull request. The rest become issues labelled [`from-review`](https://github.com/benshapyro/tycho-ai/issues?q=label%3Afrom-review), more than 120 so far, cleared before each release.
- **Rules that matter are tests, not prose.** Tests read the source and fail on a module over 500 lines, a game's word in game-neutral code, a DLL loaded outside one module, or a changed briefing. Agents start every session with no memory, so a rule that isn't checked drifts.
- **The docs are the agents' memory.** That is why there are 19 decision records for a small project: each one is what a new session reads instead of asking the maintainer again.
- **Sessions hand over in writing** ([`docs/handovers/`](../handovers/)), including what went wrong. The first note records that status summaries had missed a release blocker, and the fix: check GitHub before repeating a summary.

Things the checks caught:

- The review found a privacy promise the code didn't keep ([#313](https://github.com/benshapyro/tycho-ai/issues/313), above).
- The eval found replies that read the model's reasoning aloud, and found that it had been passing them. Both the briefing and the eval were fixed ([#192](https://github.com/benshapyro/tycho-ai/issues/192), 11 November).
- The eval for the second game found Star Citizen's words leaking into World of Warcraft answers ([#349](https://github.com/benshapyro/tycho-ai/issues/349)).
