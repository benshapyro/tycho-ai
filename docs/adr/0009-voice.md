# 9. Tycho's voice

Date: 2026-10-30 · Status: accepted

## Context

Tycho speaks confirmations, warnings and answers, and players may hear it for hours. We researched current text-to-speech options and ran a listening test with the author: 23 typical lines (plus 12 written with character) across Kokoro (local), stock ElevenLabs voices on v4 Turbo and Flash, and three voices designed for Tycho with ElevenLabs Voice Design.

Findings:
- The author clearly preferred ElevenLabs to Kokoro.
- ElevenLabs Flash v2.5 started speaking in about 0.19 s, v4 Turbo in about 0.3 s. Both cost 0.5 credits per character; full v4 costs 1.0.
- A chatty hour of play is about 8,000 characters, so about 4,000 credits on Flash or v4 Turbo.
- Kokoro took about 6 s per short line on the CPU (2 threads) with Star Citizen running, far slower than published figures (issue #18).
- Character, the Jarvis-like quality, comes mostly from how lines are written: dry wit, understatement, composure. The voice matters less.

## Decision

- **ElevenLabs is the recommended voice provider**, using the player's own ElevenLabs key.
- **All voices run on Eleven Flash v2.5**, the fastest model, at the same price as v4 Turbo. Designed voices work on Flash: The Ship's Mind started speaking in about 0.17 s, George in about 0.20 s.
- **Defaults:**
  - **The Ship's Mind** (designed for Tycho: British, calm, dry wit) is the default voice overall and the default male voice.
  - **Sarah** (American) is the default female voice.
- **The persona picker offers the rest of the shortlist:**
  - Lily (British female)
  - Brian (American male)
  - George (British male)
  - The Old Spacer (designed: wry American veteran)
- **Designed voices must be shared to ElevenLabs' public Voice Library** before other players can use them. This must happen before release for The Ship's Mind to be the default. Until then, or if it ever becomes unavailable, players get George, the stock British male voice closest to it.
- **Kokoro is the free fallback** for players without an ElevenLabs key, or offline, once its speed is fixed (issue #18).
- **Personality lives in the persona pack's writing guidance** (ADR 8), not only in the voice choice.

## Consequences

- **This amends ADR 6.** A player provides a Claude key and, for the recommended voice, an ElevenLabs key. Setup must explain what each is for. A free or no-key path still exists through the local fallback.
- A default that lives in one person's ElevenLabs account is a dependency: if it is unshared or deleted, every player falls back to George. Keep the voice's design prompt (tools/spike/voice_designs.toml) so it can be recreated.
- The ElevenLabs free plan is non-commercial and requires attribution. Players use their own keys, so this is their plan's terms, but setup should mention it.
- Text-to-speech is behind the provider interface (ADR 4), so other cloud voices can be added without changing the rest.
