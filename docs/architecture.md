# Tycho architecture

Status: living document. Decisions are recorded in [adr/](adr) (index in [adr/README.md](adr/README.md)), and this page summarises them. Anything still unproven is marked **(to measure)**.

## 1. What Tycho is

Tycho is a voice co-pilot for Star Citizen. You talk to it the way you'd talk to a crewmate. It works out what you mean, presses the right keys through your own keybinds, looks at your screen when you let it, and answers questions from live game data.

Existing tools in this space, for Star Citizen and other games, share the same failures. Tycho is built around fixing them:

1. **It knows what's going on.** Other assistants press keys blind. They can't tell whether your shields are already on, or whether you're in a seat or on foot, so "turn on shields" can turn them off. Tycho tracks state and checks before toggling.
2. **Common commands feel instant.** "Landing gear" should happen in well under half a second, with no round trip to a cloud model.
3. **It understands loose, chained and contextual requests.** "Get us ready to leave and call the tower" becomes the full startup sequence. "Do that again" and "no, the other one" work.
4. **It knows the game.** Trade, loadout, mining, location and lore answers come from live data, not a model's memory, and say where they came from.
5. **You can trust it.** It never does something irreversible by mistake. It says what it did, and it never puts your account at risk.

## 2. Goals, non-goals, anti-goals

**Goals**
- Run ship, vehicle and on-foot actions by natural speech, including multi-step sequences.
- See the game window when the user allows it, and use what it sees both to answer and to decide.
- Track game state (ship, seat, power, location, current task), with how sure it is of each fact.
- Answer game-knowledge questions from live sources: UEX Corp, the Star Citizen Wiki API, and starcitizen.tools.
- Have a voice and persona worth listening to for hours, configurable by the user.
- Be measurable: a test set of real spoken commands, with accuracy and latency tracked on every change.
- Cheap to run, with no technical setup: API keys are all a player provides (Claude, plus ElevenLabs for the recommended voice). Speech-to-text runs locally and Tycho manages it; the language model and the recommended voice run in the cloud, with a free local voice as the fallback. See [ADR 6](adr/0006-cloud-brain-local-ears.md) and [ADR 9](adr/0009-voice.md).

**Non-goals (for now)**
- Playing the game for you: flying, fighting, mining loops.
- Group or org features.
- Other games, although the core shouldn't rule them out.
- Mobile or web versions.

**Anti-goals: Tycho fails if it does any of these, even if everything else works**
- Reading or writing game memory, injecting into the game process, or anything else that could trip Easy Anti-Cheat or break CIG's terms. See [ADR 3](adr/0003-staying-within-cig-rules.md).
- Unattended automation that amounts to botting.
- An irreversible, costly or dangerous action (self-destruct, eject, jettisoning cargo, firing weapons or missiles, accepting or abandoning a contract, buying or selling) without explicit confirmation.
- Capturing the screen without permission, or keeping captures the user didn't ask to keep.
- Being so slow or unreliable that the user goes back to pressing keys.

### AI-native principles

Tycho is an AI product, not traditional software with a chatbot added. In practice:

- **Setup is a conversation.** First run finds your mics and controllers, has you say something to check your level, and has you press your push-to-talk button. The settings screen shows the result.
- **Ask or click, same result.** Every setting can be changed by voice or in the GUI, and both edit the same settings. See [ADR 5](adr/0005-settings-one-schema-three-ways-in.md).
- **It explains itself.** When something is wrong, such as a silent mic, a missing keybind or a service that's down, Tycho says what and why in plain words, instead of just failing.
- **It learns where you can see it.** Ship names, places and shorthand it picks up are listed where you can review, edit and delete them. Nothing is learned in secret.
- **Quality is measured.** Speech, understanding and actions are scored against recorded speech on every change. See section 6.

## 3. Constraints

- **Windows only.** The game runs only on Windows, and Tycho sends keypresses and captures the screen there.
- **The game has no API.** The legitimate ways in are: key and mouse input, screen capture, files the game writes (`Game.log` and the keybinding file), and outside web data.
- **It shares the PC with a demanding game.** Local speech and vision models compete with Star Citizen for the CPU and GPU. Their cost must be measured with the game running.
- **Keybinds change between patches,** and players customise them. Bindings are read from the user's own setup, never hard-coded.
- **Game data changes every patch.** Every answer should be traceable to a source and a game version.

## 4. How it works

### 4.1 The journey of one request

1. **Activation.** The user holds push-to-talk: a keyboard key, mouse button, or HOTAS or stick button. A wake word is optional, because wake words false-trigger during voice chat and gameplay noise.
2. **Speech to text.** Releasing push-to-talk ends the clip, and Parakeet transcribes it locally in about 0.1 s on the GPU. A game-term corrector then fixes words like "Art Corp" (ArcCorp). Streaming while the user speaks is a later option.
3. **Fast path.** If the phrase matches a known command ("landing gear", "open the doors", "request landing"), Tycho runs it straight away, with no model involved. Target: under 300 ms from the end of speech to the keypress.
4. **Routing.** Anything else is routed to a mode: a command, a question, a request to look at the screen, conversation, planning, a settings change, or speech not meant for Tycho. See [ADR 10](adr/0010-interaction-modes-and-routing.md).
5. **Thinking.** The brain (Claude) gets the request, the current state as Tycho understands it, recent conversation and a set of tools. It plans and calls those tools.
6. **Doing.** Tools press keys, capture the screen, look things up, or update what Tycho believes about the game.
7. **Receipt and speech.** Tycho reports what it actually did ("Gear down") and whether it confirmed the result. Replies are spoken in the persona's voice and start before the full reply is written.

### 4.2 Components

| Component | Job |
|---|---|
| Listener | Push-to-talk, optional wake word, microphone capture |
| Transcriber | Speech to text on the push-to-talk clip (Parakeet, local), plus the game-term corrector |
| Fast-path matcher | Instant matching of known commands, built from the user's keybinds |
| Router | Decides which mode a request belongs to ([ADR 10](adr/0010-interaction-modes-and-routing.md)) |
| Brain | Plans and calls tools (Claude) |
| Input | Sends scancode keypresses and cancellable sequences through the user's keybinds |
| Eyes | Captures the game window only, when allowed |
| Log reader | Tails `Game.log` and turns lines into state updates |
| State tracker | What Tycho believes is true right now (see 4.3) |
| Safety gate | The single checkpoint every action passes through (see 4.7) |
| Knowledge | Game data lookups: UEX Corp, the Wiki API, starcitizen.tools |
| Voice | Streaming text to speech (ElevenLabs, Kokoro as the local fallback), plus pre-rendered short replies ([ADR 9](adr/0009-voice.md)) |
| Memory | User preferences, fleet and habits, in a local file the user can edit |
| Control panel | Settings, command list, history and privacy controls, served locally |

Every outside service (transcription, models, voice) sits behind a small typed interface, so it can be swapped without touching the rest. Every hardware boundary (audio, input, capture, joystick) sits behind one too, so it can be tested with fakes. The measured choices are in [ADR 2](adr/0002-cascaded-voice-pipeline.md) (Outcome), [ADR 6](adr/0006-cloud-brain-local-ears.md) and [ADR 9](adr/0009-voice.md). What Tycho knows is assembled in layers, from fixed safety rules to game and persona packs ([ADR 8](adr/0008-packs-and-connections.md)).

### 4.3 Knowing the state of the game

This is the core of the design. Tycho keeps a running picture of the game: ship, seat, power, shields, landing gear, doors, quantum status, location and current mission. Each fact records **where it came from** and **how sure** Tycho is:

- **Seen:** read from a screen capture. High confidence, for a short time.
- **Logged:** from `Game.log`. High confidence, but only for events the log records.
- **Assumed:** inferred from a key Tycho pressed. Low confidence, because the game may have ignored it.

Rules:
- Before pressing a toggle whose state matters (shields, power, gear, doors, lights), check the state. If it's only assumed and a wrong guess would matter, look at the screen first if allowed, or say what it's about to do.
- After an important sequence (startup, landing), optionally check the screen to confirm it worked, and report anything that didn't.
- Facts go stale. An unconfirmed fact loses confidence over time.

### 4.4 Screen vision and permission

- **Per-request trigger:** a word the user chooses ("look", say) captures the game window together with what was said, and sends both to the brain.
- **Standing permission (off by default):** a setting that lets Tycho capture whenever the state tracker needs to confirm something.
- Only the game window is captured, never the whole desktop.
- Captures live in memory and are discarded after use, unless the user says "save that".
- An indicator in the control panel and a short sound show when a capture happens.

Vision is never on the fast path. It's for questions ("what's this contract worth?"), confirmations, and reading things the log doesn't record. **(to measure)** How long a capture plus answer takes.

### 4.5 Keybinds and actions

- Tycho reads the game's default bindings from `Data.p4k` (`Data\Libs\Config\defaultProfile.xml`), cached per game build, and applies the user's changes from `actionmaps.xml` (under `LIVE\user\client\0\Profiles\default\`) on top, reloading when that file changes. Readable action names come from the game's `global.ini`.
- Only keyboard keys and mouse buttons can be pressed. Actions bound only to a joystick, or not bound at all, can't be used; out of the box, many specific actions ("Open All Doors", "Landing System (Deploy)") have no key, so Tycho falls back to toggles ([#13](https://github.com/benshapyro/tycho-ai/issues/13)).
- It turns them into actions with friendly names and the phrases that trigger them. The user can edit the list.
- Sequences ("startup", "prepare to land") are named, editable lists of actions with waits between them.
- When an action has no binding, Tycho says so instead of guessing.
- After a patch, it rereads the file and reports actions that were added, removed or changed.

### 4.6 The router

The router must be fast and must say when it isn't sure. The modes and rules are in [ADR 10](adr/0010-interaction-modes-and-routing.md). Tested so far ([#1](https://github.com/benshapyro/tycho-ai/issues/1)): TypeSafe's Jev answers in about 0.12 s and routes questions and unclear requests well, but made 3 confident wrong single-action picks and its danger checks were unreliable. It is a candidate router only, never an actor; until it passes a re-test, Claude Haiku 5.5 decides everything the fast path doesn't.

### 4.7 Safety and confirmations

Every action passes through one safety gate, which can refuse it. The gate is independent of the models: no router or brain output can skip a confirmation. The latency spike showed why: some hosted models engaged quantum travel or fired missiles without being asked.

- Actions are graded:
  - **Free:** gear, lights, doors.
  - **Announce:** power off, flight ready, quantum engage, jump-point jumps. Travel is routine, so it isn't confirm-grade (the author's calls, 2026-10-30 and 2026-10-31); the brain still never engages it unasked.
  - **Confirm:** self-destruct, eject, jettisoning cargo, firing weapons or missiles, accepting or abandoning a contract, purchases.
  - **Shared keys:** a key that a confirm-grade action also uses is pressed only when the command first makes every such use impossible: it sets a mode that switches those controls off, and requires a logged fact that rules out the rest. Quantum engage on left click sets NAV and quantum mode, and accepts the remaining on-foot risk because Tycho only hears what the player says on purpose ([ADR 12](adr/0012-modes-before-shared-keys.md)). Otherwise the key is never pressed.

  Users can move actions between grades, but nothing leaves the confirm grade without a deliberate settings change.
- Confirmation is by voice ("confirm"), and the request expires after a few seconds.
- A spoken "stop" or "cancel", or pressing push-to-talk, immediately halts any sequence and releases every held key.
- Every action is logged with what was pressed and why, so the user can review it and say "undo that" where possible.
- If the internet or an outside service fails, the fast path keeps working, and Tycho says plainly that the rest is unavailable instead of hanging.

### 4.8 Persona and voice

The persona is configurable: name, voice, tone and how chatty it is. It keeps replies short in combat and talks more when idle. There's one default persona, kept as an editable text file.

**How Tycho addresses the player** is a setting too. The default is "Commander". Players can set a name, nickname, callsign or rank, or choose to be called nothing, by voice ("call me Ace"), in the settings screen, or in the file. A pronunciation hint covers names text-to-speech would get wrong. Setup can suggest the player's in-game handle but never uses it without asking. Tycho uses the form of address sparingly, in greetings, warnings and confirmations of big actions, not in every sentence.

The default draws on the lore. In Star Citizen's universe, humanity distrusts AI after Janus, the AI core blamed for the loss of the *Artemis*. Tycho's default persona knows this. It's careful, a little defensive about not being Janus, and always reads back what it's about to do. Its character is calm, precise and dryly witty, in the spirit of Jarvis; that comes mostly from how lines are written, and the default voice is chosen in [ADR 9](adr/0009-voice.md). Personas are packs ([ADR 8](adr/0008-packs-and-connections.md)).

## 5. Delivery stages

0. **Latency spike.** Measure the candidate stacks with the game running before committing to the architecture. **Done 2026-10-30** except the frame-rate cost; results in [ADR 2](adr/0002-cascaded-voice-pipeline.md) (Outcome) and [#1](https://github.com/benshapyro/tycho-ai/issues/1).
1. **Hands on the controls.** Push-to-talk, transcription, keybind import, the fast path, the brain with key tools, voice, the safety gate and the action log. Done when startup, landing and quantum sequences work by voice.
2. **Eyes.** The vision trigger, state tracker, log reader and check-before-toggle. Done when "turn on shields" never turns them off in the test set.
3. **Knowledge.** UEX Corp, the Wiki API and fleet memory. Done when trade, loadout and location questions are answered with a cited source.
4. **Polish.** Persona, standing vision permission, control panel, sequence editor and patch-change reports.

Settings (one schema, voice and file editing) arrive in stage 1. The settings GUI and conversational onboarding follow once the voice pipeline is proven.

## 6. How we'll know it's world-class

- **Test set:** at least 200 recorded phrases, each with the right outcome, including loose wording, chained commands and noise. Run on every change.
- **Accuracy:** the right action, or a correct "I'm not sure", on at least 95% of the test set. Zero wrong actions on confirm-grade commands.
- **Speed, from end of speech to action:**

  | Path | Target | Measured so far (spike) |
  |---|---|---|
  | Fast path | under 300 ms | about 0.1 s transcription plus about 6 ms matching; key press not yet measured |
  | Thinking, no vision | under 1.5 s | Haiku 5.5: 1.2 s typical, 2.1 s at p90 (misses the target at p90) |
  | With vision | under 4 s | not yet measured |
- **Hands-off test:** a full session (startup, travel, a mission, landing) without touching the keyboard for any action Tycho supports.
- **Game impact:** a measured, published frame-rate cost.
- **Cost:** a published cost per hour of play for each provider setup, including a free or local one.

## 7. Open questions

1. ~~Which default voice and persona tone tests best over long sessions?~~ Answered by a listening test; see [ADR 9](adr/0009-voice.md). Long-session fatigue is still untested.
2. Should Tycho ever speak unprompted, for example to warn about low fuel? If so, how do we keep it from becoming noise?
3. How much does `Game.log` record in the current patch? This sets how much the state tracker must rely on vision.
4. Can local speech-to-text run alongside the game without a noticeable frame-rate cost? **(to measure)**
5. What will CIG say when asked directly about a user-triggered voice keybind tool? See [ADR 3](adr/0003-staying-within-cig-rules.md).
