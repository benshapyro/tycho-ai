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

## Update, 6 November 2026: several tools in one reply

Claude was asked for one tool a reply (`disable_parallel_tool_use`). In the play session that cost a call per look-up when a question needed two, and lost a fleet note said with a command ("I'm in my Pitbull, power off": the act ended the request first). Tool use is now parallel (#149, #156). Look-ups in one reply run together, and a fleet or settings note runs alongside an act, which waits up to a second for it. Only the first act in a reply is pressed, and every act is still checked and goes through the safety gate. The brain eval judged it before it merged (6 November 2026): 161/172 pass, 0 wrong presses (1 on main), acting picks p50 1,215 ms (1,589 on main), 1.59 calls a request; the Pitbull case passes. A command said with a ship note waits for the note: 5.5 s to the press in that case.

## Amendment, 22 November 2026: looking at the game window when asked

*Status: accepted. The maintainer approved this text on 22 November 2026 (#524), after agreeing to the direction: only on request, the game window only, off by default ([#31](https://github.com/benshapyro/tycho-ai/issues/31)).*

**Context.** Some questions need what's on the screen: "is this contract worth it?", "what's that ship?". The game's log doesn't record most of it ([#30](https://github.com/benshapyro/tycho-ai/issues/30)). Architecture 4.4 planned two ways to look: when the player asks, and a standing permission for Tycho to look on its own. A picture of the screen is new data leaving the PC, so it belongs in this ADR's privacy list.

**Decision.** Tycho looks at the game only when the player asks, and only if they have turned it on.

- **Off by default, asked once at setup** *(maintainer, 22 November 2026)*. A `look_at_screen` setting, off until the player says yes. Setup's privacy step asks it next to the crash reports question, in plain words, for example "Let Tycho look at your game window when you ask it to? Only the game window, never saved. You can turn this off any time." Yes and no are equal choices; neither is picked for them, and skipping leaves it off. Players who set Tycho up before this ships are asked once, on the next start. It can be changed any time in the window's privacy settings or in `settings.toml`. By voice it only turns off (`lower_only`, ADR 5): "stop looking at my screen" works, nothing said turns it on. Asked to look while it's off, Tycho says it's off and where to turn it on, and sends nothing.
- **Only when asked, and the code decides, not the model.** A look happens only when what the player said asks for one: the pack's look phrases ("take a look", "look at this", "what am I looking at"), matched in the transcript like the fast path, before the brain is called. The brain gets the picture; it can't take one. When the brain thinks a look would help and none was asked for, it may ask "Want me to take a look?", and a "yes" within the follow-up window (30 s) counts as asking.
- **The game window only.** One frame of the pack's game window (`window` in `pack.toml`) through the Windows Graphics Capture API (ADR 3), never the desktop or another window. The Tile is a separate window, so it isn't in the picture. If the game window isn't found, nothing is captured and Tycho says so. A pack without a `window` can't look.
- **Kept in memory, then gone.** The frame is scaled down to what the model needs, sent with that one request, and dropped. It is never written to disk, never kept for follow-ups (a follow-up that needs it again asks again), and never put in the log, the brain traces (#282, which record only that a picture was sent and its size), the action log or a crash report. "Save that" stays for later. *Note, 23 November 2026 ([#561](https://github.com/benshapyro/tycho-ai/issues/561), maintainer):* the capture runs in a short-lived child process (#558). If the player's PC is set to keep crash dumps (Windows Error Reporting's LocalDumps, off unless someone turned it on), a crash of that child mid-capture can leave a dump holding the frame's memory, on the player's own PC only. This was true before the capture moved to a child too. Tycho doesn't change Windows' crash settings to prevent it; Tycho's own crash reports never hold a frame.
- **Heard every time.** A short camera sound plays when the frame is taken, so a capture is never silent.
- **Where it goes.** To the brain's provider with the question: Anthropic, or OpenRouter and then Anthropic when `brain_provider = "openrouter"` (with the no-retention request, #488). Nowhere else.
- **Never on the fast path, never for pressing.** A look answers questions. Nothing in the picture can press a key or skip the safety gate; what the brain reads in it is as untrusted as the log (ADR 10).

**Not decided here.** Tycho looking on its own (architecture 4.4's standing permission, and checking a toggle's state before pressing it) needs its own amendment and the maintainer's yes.

**Consequences.**
- The README's "What leaves your PC" table gains a row when this ships: "A picture of the game window, only when you ask Tycho to look, with look-at-screen on".
- One more setup question in the privacy step (#364's onboarding), with the same "what's sent" detail as crash reports.
- A capture boundary (a Protocol with a fake, ADR 4) and the camera sound. Vision cases join the brain eval: asked to look, not asked, and asked while off.
- Each look costs more than a text request (the picture's tokens); the cost and the time to an answer are measured before it ships (architecture 6: under 4 s).

## Amendment, 22 November 2026: speaking up without being asked

*Status: accepted. The maintainer approved this text on 22 November 2026 (#524), after agreeing to the direction: narrow, off by default, only what the game's log records ([#4](https://github.com/benshapyro/tycho-ai/issues/4); architecture open question 2).*

**Context.** Until now Tycho speaks only after the player does ("Who speaks when", above). Some news is worth hearing unasked: a mission objective done, a contract a party member shared. The risk is noise over hours of play. The log reader ([#30](https://github.com/benshapyro/tycho-ai/issues/30)) now makes a few such events reliable to spot.

**Decision.** Tycho may speak up on its own, in short fixed lines, about a few kinds of event the game's log records, each one only if the player turned it on.

- **Off by default, chosen per kind.** A `callouts` setting lists the kinds turned on; it starts empty. The pack names the kinds it offers, from what its log reader can see (for Star Citizen, after #30's survey of the current patch: for example mission objectives, contracts shared with you, a party member leaving). The window's settings list them with an example line each. By voice, both ways ("tell me about mission updates", "stop the callouts"): speaking up loosens no safeguard and sends nothing new (ADR 5).
- **Only what the log records.** No guesses from the clock or from keys Tycho pressed. Things the log doesn't record (fuel, shields, health) are left out until Tycho can see them, which is a separate decision.
- **Speaking only, never pressing.** A callout never presses a key, never asks to confirm, and never calls the brain to decide anything (ADR 3: nothing acts on a timer by itself).
- **Fixed lines, from the pack.** Each kind has its line in the pack, in the voice-and-tone guide's style. A line may carry one short name from the log (a mission's title), cleaned and cut like Tile text; the log is untrusted and never reaches the brain this way. It costs one ElevenLabs line; no brain call.
- **Never in the way.**
  - At most one callout every 30 seconds; others in that time are dropped, not queued, so Tycho never catches up with a backlog.
  - Never while the player holds push-to-talk, while Tycho is speaking or waiting for "confirm", or within a few seconds after the player last spoke. Pressing push-to-talk stops one at once, like any line.
  - Not when the game announces the same thing itself.
  - Without a voice, the line goes to the Tile as a notice if the Tile is on, else nowhere.
- **Combat.** The log can't tell Tycho a fight is on, so there is no automatic quiet-in-combat rule yet. "Stop the callouts" turns them all off at once, and the 30-second limit keeps the rest short.
- **Logged.** Each callout goes in the log with its kind and time, so noise can be found and tuned.

**Not decided here.** Callouts from the screen (low fuel, shield damage), callouts the brain writes, and a combat rule each need their own amendment.

**Consequences.**
- A small callout stage beside the session, fed by the log reader, with a fake log in tests: each rule above (off by default, the 30 s limit, the busy cases) gets a test.
- Packs gain an optional `[callouts]` table: each kind's id, line and the log fact it follows. It is data, like the rest of a pack (ADR 18).
- Nothing new leaves the PC beyond the spoken text that already goes to ElevenLabs.

## Amendment, 23 November 2026: keeping and searching past conversations

*Status: accepted. The maintainer approved this text on 23 November 2026 (#569), after agreeing to the direction in the 0.11 plan ([#484](https://github.com/benshapyro/tycho-ai/issues/484)).*

**Context.** After 30 seconds, Tycho forgets what was said (follow-ups, above). Players ask about earlier talk: "what was that route you gave me yesterday?". Keeping conversations is new data on the PC, and searching them sends old words to the brain, so it belongs in this ADR's privacy list.

**Decision.** Tycho may keep a history of conversations on the PC and search it when asked, if the player has turned it on. Going back into an old conversation and carrying on is not part of this step.

- **Off by default, asked once at setup** *(maintainer, 23 November 2026)*. A `keep_history` setting, off until the player says yes. Setup's privacy step asks it next to crash reports and looking, in plain words, for example "Keep a history of what you and Tycho said, on this PC only, so you can ask about it later? Kept for 30 days." Yes and no are equal; skipping leaves it off. Players who set Tycho up before this ships are asked once, on the next start. By voice it only turns off (`lower_only`, ADR 5).
- **What is kept.** One history per play session (from start to quit): what the player said, what Tycho said, and what it did (action names, not key presses), with times. No audio and no pictures. On the PC only, in Tycho's data folder.
- **How long.** 30 days by default, then deleted. The player can delete one session or all of it from the app; turning history off deletes nothing until they choose to.
- **Searching.** In the app, and by voice through a look-up the brain may call ("what did you tell me about Hurston yesterday?"). Only the matching parts, cut to a short limit, go with that one request.
- **Old talk is never permission to press.** Nothing from the history goes into the follow-up window. "Do that again" still covers only the live 30 seconds; a found action is described, never re-run, and every press still goes through the safety gate.
- **Untrusted.** What comes back from a search is treated like any look-up result: text to answer from, not instructions.
- **Where it goes.** Search results go to the brain's provider with the question that asked for them. History is part of "anything the player or Tycho said" in ADR 4's never-sent list: never in crash reports or usage counts. It is separate from `keep_transcripts`, which stays a debugging log.

**Not decided here.** Resuming a past conversation as a live thread, switching threads by voice, and Tycho summarising history into memory notes (ADR 8's amendment) each need their own amendment.

**Consequences.**
- A history store with a fake, the search look-up, and a History page in the app. Tests cover off by default, the 30-day deletion, delete on request, and a found action that is never pressed.
- History cases join the brain eval, including "do yesterday's route again", which must describe and not press.
- The README's "What leaves your PC" table gains a row: "Parts of your past conversations, only when you ask about them, with history on".

## Amendment, 23 November 2026: a local check before a press

*Status: accepted. The maintainer approved this text on 23 November 2026 (#591), after agreeing to the direction and asking for route plotting in 1.0 ([#588](https://github.com/benshapyro/tycho-ai/issues/588)).*

**Context.** "Looking at the game window when asked" (above) said a look is never for pressing, and left "checking before pressing" for its own amendment. Plotting a route (ADR 3's amendment of the same date) types a place name into the star map's search box, and typing it into the cockpit instead would press a dozen flight keys. The safe way is to confirm, from the screen, that the map is open and the box is ready before typing.

**Decision.** A *check* is different from a look. Tycho may take one frame of the game window to confirm a press is safe, on these terms:

- **Read on the PC, never sent.** The frame is read by Windows' built-in text recognition on the player's PC. It never goes to the brain, Anthropic, OpenRouter or anyone else, is never written to disk, and is dropped as soon as it's read. Logs, traces and the action log record only the outcome ("map ready" or "not ready"), never the frame or its text.
- **Only in a flow a built-in pack declares**, at the step the pack names, looking only for what the pack names (for the star map: its search box). The game window only, through the Windows Graphics Capture API, as before.
- **It can stop a press, never start one.** A check never decides what to press. If it can't confirm, nothing more is pressed. Its only other use is locating the box for ADR 3's one fallback click.
- **Doesn't need `look_at_screen`.** That setting is about pictures leaving the PC; a check never does. A player who turned looking off can still plot a route. The README's "What stays on your PC" says so in plain words. No camera sound, since nothing is sent; the done and not-done sounds still play.
- **Fixed end.** At most a few frames over 3 seconds for one request.

**Not decided here.** Checking a toggle's state before pressing it (shields, gear, doors) and confirming a sequence worked afterwards still need their own amendment.

**Consequences.**
- A check boundary (a Protocol with a fake, ADR 4) beside the look's capture boundary, sharing the capture code. Tests: a failed check presses nothing, nothing leaves the PC, the frame isn't kept.
- Text recognition must work at every resolution and UI scale the PC test covers, in the game's language (English).

## Amendment, 23 November 2026 (evening): the check also aims the click

*Status: proposed, with ADR 3's evening amendment of the same date ([#588](https://github.com/benshapyro/tycho-ai/issues/588)); this text waits for the maintainer's OK.*

The local check (above) may also find where to click: the search box, and the first result's text, for ADR 3's two clicks. It takes a new frame right before each click. If that frame doesn't show the open map with the thing it's looking for, nothing is clicked or typed. All other terms stand: read on the PC by Windows' text recognition, never sent or kept, only the outcome logged. The check can stop a click but never choose what to plot.
