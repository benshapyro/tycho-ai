# 3. Staying within CIG's rules

Date: 2026-10-29 · Status: accepted

## Context

RSI's Terms of Service (§IV) prohibit using or distributing "macro" software. In practice CIG tolerates voice keybind tools: HCS VoicePacks are officially licensed, and no bans for VoiceAttack, HCS or Wingman AI have been found. But no CIG staff statement explicitly approves tools like Tycho. The Fankit FAQ treats donations as commercial use and requires an "unofficial" disclaimer.

## Decision

- Tycho only sends keypresses through the player's own keybinds. It never reads or writes game memory, injects into the process, or hooks DirectX.
- Every action and sequence is started by the user and has a fixed end. No loops, no timers acting alone, no combat automation (aim assist, capacitor cycling).
- Screen capture uses the Windows Graphics Capture API for the game window only.
- No donations or sponsorships tied to Star Citizen IP. The README carries CIG's disclaimer. The name contains no CIG marks.
- Ask CIG support for a written position before the first public release.

## Consequences

Some "power user" automation requests will be declined by design.

## Amendment, 23 November 2026: typing a place name into the star map

*Status: accepted. The maintainer approved this text on 23 November 2026 (#591), after agreeing to the direction and asking for route plotting in 1.0 ([#588](https://github.com/benshapyro/tycho-ai/issues/588)).*

**Context.** Players want to say "plot a route to Port Tressler" and have it done. Other voice tools already do this; HCS VoicePacks, which CIG licenses, sell it. Until now Tycho only pressed the player's own keybinds. Most of plotting a route is keybinds too: the star map has its own keys (`mapui_action_planroute`, R by default), and Game.log writes "Quantum route plotted to X" with the place's name. The one new thing is typing the place's name into the star map's search box. Typing letters anywhere else would press flight keys, so it needs rules of its own.

**Decision.** Tycho may type a place's name into the game's own search box, under these rules:

- **Only in a flow a built-in pack declares.** A pack names the flow (open the map, the box, the keys after it) as data. Only a pack that ships inside Tycho may declare one (ADR 18), and Star Citizen's first is plotting a route. Never into chat, a contract, a terminal or any other box.
- **Only names from the game's own list.** The brain picks a place; the code turns it into the exact name from that patch's game data. Unknown or unclear names are asked about, never typed. Letters, digits, spaces and simple punctuation only; never Enter in the middle of a name, never a command.
- **Only after a local check says the box is ready** (ADR 14's amendment of the same date). If the check can't see the open map with the box ready, nothing is typed, and Tycho says it couldn't open the map.
- **Started by the player, with a fixed end.** One request types one name once. It may check a few times while the map opens (at most 3 seconds), then it stops. No retries on its own.
- **Proved by the game's log.** Success is Game.log's "Quantum route plotted to X" with the chosen place. Anything else is reported as not done, and the map is left open for the player.
- **The mouse, only if the keyboard can't.** If the PC test shows the search box can't be reached by keys, Tycho may make one left click on the box, located in the same frame the check read, while the check still holds. Never a click at a fixed screen position, never anywhere else, never a drag or a cursor move to steer.
- Still never game memory, injection or DirectX hooks, and still no combat automation.

**Consequences.**
- The safety gate gains a "route" flow: not confirm-grade (a route doesn't move the ship), refused with key actions off, the game window not focused, or the check failing. Every key in it still goes through `safety.check`.
- Tests: nothing typed when the check fails, a name not in the list is never typed, typing stops at its fixed end, and success needs the log line naming the place. Brain eval cases: plot a route, an unknown place, a place spelt two ways.
- Before it ships, a PC test at two resolutions and two UI scales.
- Star map changes in a patch can break the flow. When the check or the log line fails, Tycho says so and presses nothing more; it never guesses.
