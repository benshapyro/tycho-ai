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

## Decisions for Ben

Each plan lists its own, with a recommendation. In short:

1. **Mining:** keep the signature table as hand-checked pack data, crediting the community tables as a source. *Recommend yes.*
2. **Mining:** start with the player saying the number; reading it off the screen comes second. *Recommend yes.*
3. **Local data:** once the spike works, the game's own place list replaces UEX's for routes (an ADR 3 amendment). *Recommend yes, with UEX kept only while the first copy is being built.*
4. **Telemetry:** accept the ADR 3 amendment drafted on #689 (opt-in, read only, never send game actions), but only after a real build shows how it's switched on. *Recommend yes, after Phase 0.*
5. **Telemetry:** when the feed says something is already on, Tycho skips the press and says so. *Recommend yes, only with a fresh reading.*

## Ground rules for all three

- Our own code only. Sources are described as "public protocol docs and community examples"; nothing is copied or ported (AGENTS.md hard rules).
- Read only. Nothing here writes to the game, its memory or its files, except the one `user.cfg` change telemetry may need, and only with the player's yes.
- Tycho works exactly as today when a source is missing, old or unreadable. It says so once, never guesses.
- New behaviour gets tests, at least one failing on the old code, and brain evals keep zero wrong presses (#112).
