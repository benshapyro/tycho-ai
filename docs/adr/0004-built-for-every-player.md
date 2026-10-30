# 4. Built for every player, modular at the seams

Date: 2026-10-30 · Status: accepted

## Context

Tycho should work for any Star Citizen player, not just its author. Players differ in controls (keyboard and mouse, HOTAS, dual sticks), install locations and game channels, keybinds, hardware, accents and languages.

## Decision

**Nothing player-specific is hard-coded.**
- Push-to-talk accepts any keyboard key, mouse button or joystick button, bound by pressing it.
- The game install and its channels (LIVE, PTU, EPTU) are detected, with a manual override.
- Keybinds come from the player's `actionmaps.xml`, falling back to the game defaults.
- Hardware profiles (GPU, CPU only, cloud only) are detected and choose sensible defaults.
- Settings live in a versioned file in the user's app-data folder, separate from code.
- User-facing text isn't hard-coded in logic, so other languages stay possible.

**Modular where things vary, fixed where trust lives.**
- These sit behind a `typing.Protocol` with a test fake, so adding one means adding an adapter:
  - input devices
  - speech-to-text, LLM and text-to-speech providers
  - screen capture
  - game data sources
  - personas
- The safety gate, state tracker and action pipeline are core and not pluggable. Nothing may bypass them.
- No general plugin system until real third-party demand appears.

**Measured on more than one voice.** The test set grows beyond the author's voice, through community recordings contributed with explicit consent.

**Private by default.** No telemetry. Diagnostics are opt-in bundles the user reviews before sharing.

## Consequences

- More setup UX to build: a binding wizard and install detection.
- Every adapter needs a fake and contract tests, which keeps CI hardware-free.
