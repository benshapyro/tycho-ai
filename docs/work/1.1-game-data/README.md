---
status: draft
parent: none
---

# 1.1 game data: three plans

Three features came out of the 26 November 2026 audit. Each has a plan here; none is built yet. They're on the 1.1 milestone and wait until 1.0 ships.

| Plan | What the player gets | Can start | Blocked on |
|---|---|---|---|
| [Mining scan signatures](mining-scan.md) ([#691](https://github.com/benshapyro/tycho-ai/issues/691)) | "What's 10,620?" gets "Beryl, three rocks" | Now (works on LIVE) | Nothing |
| [Local game data](local-game-data.md) ([#690](https://github.com/benshapyro/tycho-ai/issues/690)) | Routes use the player's own patch data, not UEX | Now (a spike first) | Ben's ADR 3 call before it replaces UEX |
| [Game telemetry](game-telemetry.md) ([#689](https://github.com/benshapyro/tycho-ai/issues/689)) | "Doors open" instead of "I can't see which way it went" | Only when a game build has it | CIG turning it on; Ben's ADR 3 call |

**Recommended order:** mining scan first (smallest, no ADR change, useful today), then local game data, then telemetry when CIG ships it.

## Ben's decisions (28 November 2026)

Ben answered in the project thread on 28 Nov 2026 (**stated**):

1. **Order:** mining scan, then local game data, then telemetry.
2. **Mining numbers:** our own hand-checked table in the pack, each entry naming its patch, crediting the community tables.
3. **Mining, how Tycho gets the number:** both at once. Saying the number and Tycho reading it off the screen ship together.
4. **Mining look phrases:** scan-specific words only ("what's that signature", "what am I scanning").
5. **Local data:** yes, the game's own place list replaces UEX for routes, with UEX only while the first copy builds. Ben added: a local database Tycho controls is better in general (no web search every time, better tools on top), with UEX, the Wiki API and web search kept as backup and refresh. **Benchmarks and evals must show it's better.** See [local-game-data.md](local-game-data.md) and #740.
6. **Telemetry ADR 3 amendment:** agreed in principle now; the final text is approved after Phase 0, so it names the real switch.
7. **Telemetry behaviour:** Tycho may write the `user.cfg` lines after asking, with a backup and an undo; and Tycho skips a press when a reading under a second old shows it's already done.

The ADR texts themselves still need Ben's approval when their PRs come.

## Ground rules for all three

- Our own code only. Sources are described as "public protocol docs and community examples"; nothing is copied or ported (AGENTS.md hard rules).
- Read only. Nothing here writes to the game, its memory or its files, except the one `user.cfg` change telemetry may need, and only with the player's yes.
- Tycho works exactly as today when a source is missing, old or unreadable. It says so once, never guesses.
- New behaviour gets tests, at least one failing on the old code, and brain evals keep zero wrong presses (#112).
