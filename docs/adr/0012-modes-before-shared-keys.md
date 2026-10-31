# 12. Keep the player's bindings; set the mode before a shared key

Date: 2026-10-31 · Status: accepted

## Context

To engage quantum by voice, the first Tycho profile gave quantum engage its own key, Right Alt+Q. That quietly took the action off left click. Star Citizen keeps the keyboard and the mouse in one binding slot per action, so adding a keyboard key replaced the default mouse button. The profile builder checked only for an existing keyboard key, and Tycho's keybind reader treated the two devices separately, so neither caught it. The author found it in game, as his left click had stopped engaging quantum.

Every player has quantum engage on left click by default, and few will change it. But left click also does other things:
- fires guns, missiles, and mining and salvage lasers, depending on the operator mode;
- fires a handgun, or throws a punch, on foot;
- requests a jump at a jump point.

The safety gate refused to press any key that a confirm-grade action shares. That made voice quantum engage impossible on default bindings.

Game.log, checked on 2026-10-31, records:
- a HUD notification when quantum calibration starts and when it completes;
- a line when the player leaves a pilot seat;
- nothing about entering a seat, the operator mode, NAV or SCM, or weapons.

A cancelled calibration isn't logged.

## Decision

1. **Tycho never takes a key or button from the player.** The profile builder adds keys only to actions with no keyboard key and no mouse button, defaults included, and reports the others as left alone. The keybind reader models one keyboard and mouse slot per action. `make_layout.py --restore` hands an action back to its defaults. The author's quantum engage went back to left click this way.
2. **A command can set the game's mode before its main action.** Commands get:
   - `setup`: specific "set" actions, never toggles, each on its own key;
   - `settle_seconds`: how long the switch takes;
   - `requires`: facts the game state must show.

   "Engage quantum" sets NAV, waits 1.2 s (the switch takes about a second), sets quantum mode, then clicks.
3. **The gate allows a shared key only when every dangerous use of it is switched off.** It knows which control contexts each setup action disables:
   - NAV disables ship weapons and missiles;
   - quantum mode also disables mining and salvage.

   It also knows which contexts each required fact rules out: "quantum calibrated, still seated" rules out on foot. Any other confirm-grade action left on the key still refuses the press. Without the setup, left click is refused as before.
4. **Game.log is a narrow fact source.** `tycho.game.gamelog.GameLog` reads the file incrementally on demand and reports `quantum_calibrated`:
   - set when calibration completes;
   - cleared by a new calibration, leaving the seat, or a game restart.

   It only reads a file the game writes (ADR 3). Unrecognised lines leave facts unknown, so a patch that changes the wording makes quantum engage refuse rather than guess.
5. **Jump-point jumps aren't confirm-grade.** They're routine travel (the author's call), as quantum engage became on 2026-10-30.

## Consequences

- Players keep their muscle memory, and the profile only fills empty slots.
- Voice quantum engage adds about 1.5 s for the mode switches, even when the ship is already in NAV and quantum mode. Tycho can't see the mode, so it always waits.
- "Calibrated" can be stale after a cancelled calibration. The click then happens in NAV and quantum mode with nothing to engage, which is harmless.
- The guard tables in `safety.py` are game knowledge, kept by hand. A patch that makes NAV fire weapons would make them wrong. They're short, and each entry has its reason.
- Other context-dependent keys can use the same pattern: set a mode that switches off the danger, then press.

## Correction, 2026-10-31: a plotted route, not calibration

The first in-game test refused every "engage quantum": Game.log never showed the drive calibrated. The calibration notifications found earlier were all "… By SpaceEdd", another player's ship. The game doesn't log the player's own calibration at all. That should have been checked against the player's handle before deciding.

The fact is now `quantum_route_set`:
- set by "Successfully calculated route to …", which the game logs when the player plots a route;
- cleared by "Arrived at Final Destination", leaving the pilot seat, or a game restart.

It rules out on foot for the same reason as before, and the setup still switches off the ship's weapons. Clicking before calibration finishes, or with a stale route, happens in NAV and quantum mode, where it engages nothing. Replaying the author's log, all five refused attempts would have been allowed.

## Second correction, 2026-10-31: no log check at all

Requiring a plotted route meant plotting one before every voice jump. The author ruled that out: "engage quantum" is a phrase nobody says on foot by accident. So `quantum_engage` requires nothing from Game.log. Instead it lists `accepted_risks = ["player"]`: the author accepts that left click could fire a drawn handgun on foot.

Only `player` can be accepted (`safety.ACCEPTABLE_RISKS`). Ship weapons, missiles, mining and salvage must still be switched off by the setup, and any other confirm-grade action on the key still refuses the press. Everyday phrases ("jump", "punch it") were removed, so only the deliberate ones are left.

The Game.log reader and its `quantum_route_set` fact stay, for later commands.
