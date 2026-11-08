# 9. Tycho's voice

Date: 2026-10-30 · Status: accepted; defaults amended 2026-11-08 (see the end)

## Context

Tycho speaks confirmations, warnings and answers, and players may hear it for hours. We researched current text-to-speech options and ran a listening test with the author: 23 typical lines (plus 12 written with character) across Kokoro (local), stock ElevenLabs voices on v4 Turbo and Flash, and three voices designed for Tycho with ElevenLabs Voice Design.

Findings:
- The author clearly preferred ElevenLabs to Kokoro.
- ElevenLabs Flash v2.5 started speaking in about 0.19 s, v4 Turbo in about 0.3 s. Both cost 0.5 credits per character; full v4 costs 1.0.
- A chatty hour of play is about 8,000 characters, so about 4,000 credits on Flash or v4 Turbo.
- Kokoro took about 6 s per short line on the CPU (2 threads) with Star Citizen running, far slower than published figures ([#18](https://github.com/benshapyro/tycho-ai/issues/18)).
- Character, the Jarvis-like quality, comes mostly from how lines are written: dry wit, understatement, composure. The voice matters less.

## Decision

- **ElevenLabs is the recommended voice provider**, using the player's own ElevenLabs key.
- **The default model is Eleven v4 Turbo** for every voice, with **Flash v2.5 as an optional "fastest" setting**.
  - Flash is about 0.1 s quicker to first audio (The Ship's Mind 0.17 s, George 0.20 s, versus about 0.3 s on v4 Turbo), at the same price.
  - In the listening test Flash rushed and mispronounced some words in Sarah's lines.
  - That 0.1 s is small next to the brain's ~1.2 s, and the most frequent short confirmations are pre-rendered anyway.
  - Speaking speed is a separate per-voice setting (ElevenLabs allows about 0.7× to 1.2×), so a quicker pace doesn't need Flash.
- **Defaults:**
  - **The Ship's Mind** (designed for Tycho: British, calm, dry wit) is the default voice overall and the default male voice. *Amended 8 November 2026: George on Flash v2.5.*
  - **Sarah** (American) is the default female voice. *Amended 8 November 2026: Sarah on v4 Turbo.*
- **The persona picker offers the rest of the shortlist:**
  - Lily (British female)
  - Brian (American male)
  - George (British male)
  - The Old Spacer (designed: wry American veteran)
- *Superseded 8 November 2026: ElevenLabs doesn't allow this.* **Designed voices must be shared to ElevenLabs' public Voice Library** before other players can use them. This must happen before release for The Ship's Mind to be the default. Until then, or if it ever becomes unavailable, players get George, the stock British male voice closest to it.
- **Kokoro is the free fallback** for players without an ElevenLabs key, or offline, once its speed is fixed ([#18](https://github.com/benshapyro/tycho-ai/issues/18)). kokoro-onnx phonemises some words through espeak-ng, which is GPL, so the shipped fallback must not bundle espeak-ng (use a dictionary plus Tycho's own lexicon, or another model).
- **Personality lives in the persona pack's writing guidance** (ADR 8), not only in the voice choice.

## Consequences

- **This amends ADR 6.** A player provides a Claude key and, for the recommended voice, an ElevenLabs key. Setup must explain what each is for. A free or no-key path still exists through the local fallback.
- A default that lives in one person's ElevenLabs account is a dependency: if it is unshared or deleted, every player falls back to George. Keep the voice's design prompt (tools/spike/voice_designs.toml) so it can be recreated.
- The ElevenLabs free plan is non-commercial and requires attribution. Players use their own keys, so this is their plan's terms, but setup should mention it.
- Text-to-speech is behind the provider interface (ADR 4), so other cloud voices can be added without changing the rest.
- **Kept lines (2 November 2026).** ElevenLabs has no cheaper model than v4 Turbo (Flash costs the same per character), so the saving is in not paying twice for one line. Lines of up to 40 characters ("Gear down.", the filler while a search runs) are kept as audio in `%LOCALAPPDATA%\tycho\voice`, one file per voice, model and wording. The next time, they play from disk at once, for free. The folder is capped at 10 MB (about 140 lines), and the line used longest ago is dropped first. Only complete lines are kept.

## Amendment: default voices (8 November 2026)

**Context.** ElevenLabs' current docs say voices made with Voice Design, and Instant Voice Clones, can't be published to the public Voice Library. Only Professional Voice Clones can, and those are made from a real person's recorded voice. A designed voice can be shared only with members of its owner's workspace ([Voice Library](https://elevenlabs.io/docs/eleven-creative/voices/voice-library), [Who has access to my voices?](https://elevenlabs.io/docs/help-center/product/voices/my-voices/who-has-access-to-my-voices)). So The Ship's Mind can't be a default that other players' keys can reach, and #25 can't be done as written.

**Decision.**
- **George on Flash v2.5** is the default voice overall and the default male voice. It's the stock British voice closest to The Ship's Mind, every account has it, and in the listening test George was the voice tried on Flash, at about 0.20 s to first audio.
- **Sarah on v4 Turbo** is the default female voice. Flash rushed and mispronounced words in Sarah's lines, so she stays on v4 Turbo.
- So the model is chosen **per voice**, not once for every voice. v4 Turbo stays the model for any voice without its own.
- **The voice is a setting** (#242): an ElevenLabs voice ID, or a shortlist name. A designed voice, The Ship's Mind or The Old Spacer, works only for the account that made it. Its design prompt is kept in `tools/spike/voice_designs.toml` so a player can design their own, though each design comes out a little different.
- Tycho checks at start that the chosen voice is available to the player's key, and falls back to George if it isn't. It says so in the log, in `tycho doctor` and in the window.

**Consequences.**
- No default depends on one person's account, which removes the risk this ADR names under Consequences.
- The persona picker's shortlist is unchanged, except that the designed voices are marked "your own account only".

