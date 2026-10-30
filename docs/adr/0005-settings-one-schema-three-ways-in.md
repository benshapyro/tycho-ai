# 5. Settings: one schema, three ways in

Date: 2026-10-30 · Status: accepted

## Context

Players will want to choose their microphone, push-to-talk button, voice, persona, providers and much more, and that list will keep growing. Ultimo is an AI product, so people will expect to change things by asking ("use my headset mic") as well as by clicking. A file is still the right tool for power users, backups and sharing setups.

If the GUI, voice and file each kept their own idea of the settings, they would drift apart, and voice changes would be invisible or impossible to undo.

## Decision

**One typed settings model is the single source of truth.**
- It's defined once in code, with types, defaults, allowed values and a description of each setting.
- It's stored as a versioned TOML file in the user's app-data folder (see [ADR 4](0004-built-for-every-player.md)), with migrations between versions.
- Changes are validated against the schema, whichever way they come in.

**Three ways to edit it, all equal:**
1. **GUI.** A settings screen built from the schema, so new settings appear without new screen code.
2. **Voice.** The model gets tools to read and change settings. A change goes through the safety gate like any other action, gets a spoken receipt ("Mic changed to Arctis Pro. Say 'undo' to revert.") and shows up in the GUI at once.
3. **File.** Hand-edited TOML, reloaded when it changes, with clear errors if it's invalid.

**Every change is recorded and reversible.** There's one change log with the source (GUI, voice or file), old and new values, and undo.

**Some settings can't be changed by voice.** Anything that loosens safety (confirmation rules, standing vision permission, API keys) needs the GUI. A misheard sentence must never weaken a safeguard.

## Consequences

- The schema needs care up front: names, descriptions and grouping are user-facing.
- A schema-driven GUI looks less hand-crafted. Key screens (onboarding, push-to-talk binding) can still be custom.
- Until the GUI exists, command-line flags such as the recorder's `--device` stand in for it.
- GUI technology is a separate decision, made when stage 1 needs it.
