# 8. Packs and connections

Date: 2026-10-30 · Status: proposed

## Context

Tycho starts with Star Citizen but may grow to other games, each with its own world, vocabulary, controls, data and atmosphere. Players will also want to shape Tycho themselves: its voice and sound, what it knows about them and their org, and which outside services it can reach, such as their org's Discord. ADR 4 already makes personas and data sources swappable. This decides how all of that fits together.

## Decision

**What Tycho knows and how it sounds is assembled from layers, in this order:**

1. **Core (fixed).** Tycho's behaviour and the safety rules: confirmations, the never-press list, the fact that every action starts from the player. Nothing below can change or remove these. A pack that says "eject without asking" is ignored and reported.
2. **Game pack.** Everything specific to one game:
   - a briefing on its setting and lore;
   - game terms for the speech corrector;
   - how to find and read keybinds;
   - action categories, and which actions count as dangerous;
   - fast-path commands;
   - data sources (UEX and the Star Citizen Wiki for Star Citizen).
   Star Citizen is the first pack.
3. **Persona pack.** Voice, sound treatment (a filter chain with presets), speaking style, form of address and sound cues. The default persona suits its game; players can swap or customise it.
4. **Player layer.** The player's own notes ("my org is X, I mostly fly a Constellation"), parts of packs switched on or off, and edits to the briefing.
5. **Live state.** What Tycho currently knows about the game: ship, location, recent events.

**Connections to outside services use MCP** (Model Context Protocol), the open standard for connecting assistants to tools, so the community can add connections without changing Tycho's code. Each connection is off until the player turns it on, and has its own permissions: read only, or allowed to act. Anything that speaks for the player, such as posting in Discord, always asks first.

**Packs are data, not code.** A pack is a folder of text and settings files that Tycho validates on load. Running code from a pack is out of scope; if it's ever needed, it goes through a connection instead.

## Consequences

- The Star Citizen-specific parts of the code (keybind reading, the lexicon, fast-path commands) move behind a game-pack interface over time.
- The prompt is assembled from layers, which keeps the core cacheable and stable, and makes it testable which layer said what.
- Audio settings (volume, filter preset and strength) and persona choices join the settings schema (ADR 5).
- Community packs and connections raise trust questions (what a pack can make Tycho say, what a connection can see), to be settled before any sharing feature.
