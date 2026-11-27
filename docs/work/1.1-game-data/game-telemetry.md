---
status: draft
parent: docs/work/1.1-game-data/README.md
---

# Plan: game state from the game's local telemetry feed (#689)

## The idea

Tycho can say what it pressed, not what happened. Star Citizen 5.0 PTU added a local, read-only feed that reports doors, landing gear, power, shields, NAV or SCM mode and whether you're seated. Reading it lets Tycho say what really happened.

Example: "open the doors". Today: "Pressed doors. I can't see which way it went." With telemetry: "Doors open." And "turn on shields" with shields already on: "Shields are already on", nothing pressed.

## What we know

- A local server on `127.0.0.1:2942` (service `sc.game.telemetry.v1.GameTelemetryService`, stream `TelemetryStream`), seen in PTU 5.0 build 13238477 on 20 Nov 2026. Source: public protocol docs and community examples (**stated**).
- A forum reply on 25 Nov says CIG switched it off again in the latest PTU. No official docs (**stated**).
- Not on LIVE: Ben's LIVE Game.log (build 13165946) has no "registered" line (**verified** on that log).
- How it's switched on is **unknown**: either three `user.cfg` lines or an environment variable, per community reports. Proof it's on: Game.log writes `Service 'sc.game.telemetry.v1.GameTelemetryService' registered`.
- About 17 updates a second, only after spawning. Quirks to handle: on-foot state flickers at a door (wait 1.5 s), the ship's data arrives 6 to 9 s after sitting, the layout can change with a patch.
- It also accepts game actions (`TriggerGameAction`). Tycho must never use that (ADR 3).

## The plan, step by step

**Phase 0: proof on the PC (no code).** When a LIVE or PTU build has it, turn it on, confirm the Game.log line, and record two minutes of the feed while sitting down, powering up, opening doors, lowering gear and making one jump. This settles the real switch and gives test recordings. *Blocked on CIG.*

**Phase 1: read four facts.** A new game-state source in `games/star_citizen/` beside the Game.log reader: seated, NAV or SCM, doors, gear. Each fact carries where it came from and when. Our own code decodes only the fields we use, from the few field numbers in public docs, or CIG's own description once published; an unknown layout is reported as "incompatible", never guessed. It connects only to `127.0.0.1`, sends only the empty request, and reconnects quietly if the game restarts. Off by default; Settings has a switch, and setup explains it. If turning it on needs `user.cfg` lines, Tycho offers to add them, asks first, and keeps a backup. One new dependency: `grpcio` (Apache-2.0) to talk to the server.

**Phase 2: say what happened.** After a toggle press, wait up to about one second for the matching fact to change. Changed: say the result ("Doors open"). No change, or no feed: say what #29 says today. Never claim more than the feed showed.

**Phase 3: check before a toggle.** If a fresh reading (under a second old) already shows the asked-for state, press nothing and say so. A stale or missing reading: press as today.

**Phase 4: speaking up.** Callouts from telemetry facts, inside ADR 14's limits (only kinds the player turned on).

**Who speaks for what:** Game.log where it names things (routes, places, blueprints), telemetry where state or timing matters. One source per topic, so Tycho never says two different things.

## Tests and eval

- Recorded frames from Phase 0 replayed through a fake local server in tests. Until then, hand-made frames.
- The fake server sending an unknown layout: Tycho reports it and carries on with Game.log.
- At least one test fails on old code (a toggle with the feed showing the change says the result).
- Brain cases: state says shields on (no press), state unknown (press, said honestly). Zero wrong presses still holds.

## Decisions for Ben

1. **The ADR 3 amendment** drafted on #689: Tycho may read the feed, off until the player turns it on, read only, never sends game actions. *Recommendation:* approve the idea now, approve the final text after Phase 0, so it names the real switch.
2. **Tycho writing `user.cfg` for the player.** *Recommendation:* yes, only after asking, with a backup and a way to undo in Settings. Players shouldn't need to edit game files by hand.
3. **Skipping a press when the feed says it's already done** (Phase 3). This changes what a command does. *Recommendation:* yes, only with a reading under a second old; otherwise press as today.

## Size

Phase 0: one PC session when a build has it. Phases 1 and 2: two PRs. Phase 3: one PR plus eval cases. Phase 4: later.
