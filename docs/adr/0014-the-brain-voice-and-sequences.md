# 14. The brain, its voice, and sequences

Date: 2026-10-31 · Status: accepted

## Context

After 0.2, Tycho does only what the fast path recognises: 82 commands with fixed phrasings. Anything said differently, misheard, or not a command plays the "not done" sound. Architecture stage 1 ends when startup, landing and quantum sequences work by voice, and that needs three things together:

- **A brain**, for requests the fast path can't match. ADR 6 chose Claude Haiku 5.5 at low effort: 97% on the 82-case eval, about 1.2 s typical and 2.1 s at p90 ([#21](https://github.com/benshapyro/tycho-ai/issues/21)).
- **A voice.** A brain that answers questions or asks "which door?" is of little use silently. ADR 9 chose ElevenLabs.
- **Sequences.** Multi-step routines like "startup" must be predictable, but players won't all say them the same way.

The author decided the open questions in a design interview (2026-10-31). The decisions marked *(author)* are theirs; the rest are the implementer's, open to veto.

## Decision

**0.3 ships the brain, the voice and sequences together.**

### The brain

- **Routing:** the fast path first, then Claude Haiku 5.5 at effort low, with the player's Anthropic key. Jev stays out until its re-test (ADR 10).
- **Briefing:** a cached system prompt with the persona's rules, the player's bound actions, the commands and the sequences. It is rebuilt only when the keybinds or the command files change, so the cache holds.
- **Tools Claude can call:**
  - `run_sequence(name)`: a named sequence (below).
  - `run_actions(actions)`: bound actions, in order.
  - Otherwise Claude replies in a sentence or two of text, which Tycho speaks.
- **What Claude picks is turned back into commands before the gate.** An action that is a fast-path command's own action runs as that command, with its setup, waits and accepted risks. So "get us to the next planet" runs `quantum_engage` exactly as the fast path would. Any other action runs bare: no setup and no accepted risks, so a key shared with a confirm-grade action is refused.
- **Everything Claude picks goes through `safety.check`,** like the fast path, and is planned as a whole before anything is pressed. A confirm-grade pick asks the question sound and waits for "confirm" (ADR 13).
- **Weapons** *(author)*: Claude may choose to fire weapons or launch missiles. These are confirm-grade, so every such request waits for the player's spoken "confirm", which suits one-off requests ("launch a missile"), not a fight.
- **Never unasked:** quantum and jump engage, power off and the confirm grade are in the briefing as "only when clearly asked". The eval checks this; the gate can't, because it can't tell what the player meant.
- **Travel the brain picked asks first** *(author, 2026-10-31)*: quantum or jump engage chosen by Claude waits for "confirm", because its reading of looser words ("get us out of here") could be wrong. The fast path's own phrases ("engage quantum") stay instant.
- **Speed** ([#21](https://github.com/benshapyro/tycho-ai/issues/21)): Tycho presses as soon as the tool call's action list has streamed, before Claude finishes writing anything else. The briefing is cached for an hour (play is bursty) and one warm-up request at startup writes it and opens the connection; measured, a pick then takes about 1.1 to 1.3 s. Claude doesn't confirm risky actions itself; the briefing says the gate does.
- **Follow-ups** *(author)*: for 30 seconds after a request, the next one is sent with the last few exchanges (what was said, what Tycho did or said), so "do that again" and "no, the ramp" work. After 30 s of quiet, Claude starts fresh.
- **Questions** *(author)*: with no live data until stage 3, Claude answers Star Citizen questions from its own knowledge and says when an answer may be out of date ("last I knew…"). It never invents prices.
- **When the brain is unavailable** (no key, offline, an error, or no answer within 8 s), Tycho plays "not done", logs why, and the fast path keeps working.

### The voice

- **ElevenLabs only in 0.3** *(author)*, on Eleven v4 Turbo with The Ship's Mind (ADR 9), streamed so speech starts before the whole line has arrived. Kokoro waits for [#18](https://github.com/benshapyro/tycho-ai/issues/18).
- **Who speaks when** *(author)*:
  - Fast-path commands keep the done and not-done sounds: instant and quiet.
  - When Claude acted, it says briefly what it did ("Gear down, lights on"), so the player hears how they were understood. The line is spoken after the press starts, never before.
  - **Unless the game announces it itself** *(author, 2026-10-31)*. The ship's computer already speaks for power, engines, weapons power, landing gear and flight ready, and air traffic control answers its own requests. Those commands are marked `announced = true` in `commands.toml`, and when everything Claude did is announced, Tycho plays the done sound instead of talking over the game.
  - Replies and questions from Claude are spoken.
- **Pressing push-to-talk stops Tycho talking at once** (ADR 10), and cancels anything in flight, a key sequence included, as it always has (architecture 4.7). *(Corrected 2026-11-01: this line first said the sequence carried on, which the code never did.)*
- **Questions are spoken too** *(author, 2026-11-01)*: "Eject? Say confirm.", in the gate's own words (ADR 13).
- **Without an ElevenLabs key, or offline,** Tycho plays the done or not-done sound, and the reply goes to the log.

### Sequences

- **A sequence is a named, fixed list of commands, with waits between them,** in `commands.toml` beside the commands. For example, startup is power, then engines, then flight ready.
- **The fast path matches a sequence's phrasings,** like any command. For any other wording or mishearing, Claude gets the request and calls `run_sequence("startup")`. Claude only chooses which sequence; the steps always come from the file, so the outcome is deterministic.
- **Players can edit them** *(author)*: a user `commands.toml` in Tycho's settings folder adds commands and sequences, or replaces built-in ones by id. Editing them by voice comes with the settings work.
- **Each step goes through the gate,** and the whole sequence is planned before the first press, so a refused or unbound step stops it before it starts, exactly like a chain.

### Privacy

What leaves the PC, for the README's list:
- **To Anthropic:** the transcript, the recent exchanges, and the bound action and command list.
- **To ElevenLabs:** the text Tycho speaks.
- Audio never leaves the PC.

## Done when

- Startup, landing and quantum work by voice in game, said in the author's own words, not only the listed phrasings.
- A phrasing the fast path misses, such as "drop the landing gear, would you", is pressed by Claude, and Tycho says what it did.
- A question ("what's a good mining ship?") gets a spoken answer.
- The brain eval has zero wrong presses, including nothing confirm-grade or travel pressed unasked.
- From the end of speech, the first press on the brain path is p50 ≤ 1.5 s, and speech starts within 2 s.

## Consequences

- `anthropic` moves from the bench group to the app's dependencies. ElevenLabs needs an HTTP client for streaming.
- The brain and the voice are new boundaries (`Brain`, `Speaker`), each a Protocol with a fake, so the whole loop stays testable offline.
- The action log records the brain's picks and its spoken line, so misroutes can be added to the eval (ADR 10).
- Players need two keys for the full experience, and setup must explain both. Without either, Tycho is the 0.2 fast path.
