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
