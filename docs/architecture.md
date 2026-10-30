# Ultimo architecture

Status: draft. Decisions are recorded in [adr/](adr). Anything still unproven is marked **(to measure)**.

## 1. What Ultimo is

Ultimo is a voice co-pilot for Star Citizen. You talk to it the way you'd talk to a crewmate. It works out what you mean, presses the right keys through your own keybinds, looks at your screen when you let it, and answers questions from live game data.

Existing tools in this space, for Star Citizen and other games, share the same failures. Ultimo is built around fixing them:

1. **It knows what's going on.** Other assistants press keys blind. They can't tell whether your shields are already on, or whether you're in a seat or on foot, so "turn on shields" can turn them off. Ultimo tracks state and checks before toggling.
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
- Cheap to run, with no technical setup: an API key is all a player provides. Speech-to-text runs locally and Ultimo manages it; the language model runs in the cloud. See [ADR 6](adr/0006-cloud-brain-local-ears.md).

**Non-goals (for now)**
- Playing the game for you: flying, fighting, mining loops.
- Group or org features.
- Other games, although the core shouldn't rule them out.
- Mobile or web versions.

**Anti-goals: Ultimo fails if it does any of these, even if everything else works**
- Reading or writing game memory, injecting into the game process, or anything else that could trip Easy Anti-Cheat or break CIG's terms. See [ADR 3](adr/0003-staying-within-cig-rules.md).
- Unattended automation that amounts to botting.
- An irreversible or costly action (self-destruct, eject, accepting or abandoning a contract, buying or selling) without explicit confirmation.
- Capturing the screen without permission, or keeping captures the user didn't ask to keep.
- Being so slow or unreliable that the user goes back to pressing keys.

### AI-native principles

Ultimo is an AI product, not traditional software with a chatbot added. In practice:

- **Setup is a conversation.** First run finds your mics and controllers, has you say something to check your level, and has you press your push-to-talk button. The settings screen shows the result.
- **Ask or click, same result.** Every setting can be changed by voice or in the GUI, and both edit the same settings. See [ADR 5](adr/0005-settings-one-schema-three-ways-in.md).
- **It explains itself.** When something is wrong, such as a silent mic, a missing keybind or a service that's down, Ultimo says what and why in plain words, instead of just failing.
- **It learns where you can see it.** Ship names, places and shorthand it picks up are listed where you can review, edit and delete them. Nothing is learned in secret.
- **Quality is measured.** Speech, understanding and actions are scored against recorded speech on every change. See section 6.

## 3. Constraints

- **Windows only.** The game runs only on Windows, and Ultimo sends keypresses and captures the screen there.
- **The game has no API.** The legitimate ways in are: key and mouse input, screen capture, files the game writes (`Game.log` and the keybinding file), and outside web data.
- **It shares the PC with a demanding game.** Local speech and vision models compete with Star Citizen for the CPU and GPU. Their cost must be measured with the game running.
- **Keybinds change between patches,** and players customise them. Bindings are read from the user's own setup, never hard-coded.
- **Game data changes every patch.** Every answer should be traceable to a source and a game version.

## 4. How it works

### 4.1 The journey of one request

1. **Activation.** The user holds push-to-talk: a keyboard key, mouse button, or HOTAS or stick button. A wake word is optional, because wake words false-trigger during voice chat and gameplay noise.
2. **Speech to text.** Text streams as the user speaks. Releasing push-to-talk marks the end of speech.
3. **Fast path.** If the phrase matches a known command ("landing gear", "open the doors", "request landing"), Ultimo runs it straight away, with no model involved. Target: under 300 ms from the end of speech to the keypress.
4. **Sorting.** Anything else goes to a quick sorter: a game action, a question, a request to look at the screen, conversation, or noise to ignore.
5. **Thinking.** The brain (Claude) gets the request, the current state as Ultimo understands it, recent conversation and a set of tools. It plans and calls those tools.
6. **Doing.** Tools press keys, capture the screen, look things up, or update what Ultimo believes about the game.
7. **Receipt and speech.** Ultimo reports what it actually did ("Gear down") and whether it confirmed the result. Replies are spoken in the persona's voice and start before the full reply is written.

### 4.2 Components

| Component | Job |
|---|---|
| Listener | Push-to-talk, optional wake word, microphone capture |
| Transcriber | Streaming speech to text |
| Fast-path matcher | Instant matching of known commands, built from the user's keybinds |
| Sorter | Decides what kind of request it is |
| Brain | Plans and calls tools (Claude) |
| Input | Sends scancode keypresses and cancellable sequences through the user's keybinds |
| Eyes | Captures the game window only, when allowed |
| Log reader | Tails `Game.log` and turns lines into state updates |
| State tracker | What Ultimo believes is true right now (see 4.3) |
| Safety gate | The single checkpoint every action passes through (see 4.7) |
| Knowledge | Game data lookups: UEX Corp, the Wiki API, starcitizen.tools |
| Voice | Streaming text to speech, plus pre-rendered short replies |
| Memory | User preferences, fleet and habits, in a local file the user can edit |
| Control panel | Settings, command list, history and privacy controls, served locally |

Every outside service (transcription, models, voice) sits behind a small typed interface, so it can be swapped without touching the rest. Every hardware boundary (audio, input, capture, joystick) sits behind one too, so it can be tested with fakes. The starting providers are in [ADR 2](adr/0002-cascaded-voice-pipeline.md).

### 4.3 Knowing the state of the game

This is the core of the design. Ultimo keeps a running picture of the game: ship, seat, power, shields, landing gear, doors, quantum status, location and current mission. Each fact records **where it came from** and **how sure** Ultimo is:

- **Seen:** read from a screen capture. High confidence, for a short time.
- **Logged:** from `Game.log`. High confidence, but only for events the log records.
- **Assumed:** inferred from a key Ultimo pressed. Low confidence, because the game may have ignored it.

Rules:
- Before pressing a toggle whose state matters (shields, power, gear, doors, lights), check the state. If it's only assumed and a wrong guess would matter, look at the screen first if allowed, or say what it's about to do.
- After an important sequence (startup, landing), optionally check the screen to confirm it worked, and report anything that didn't.
- Facts go stale. An unconfirmed fact loses confidence over time.

### 4.4 Screen vision and permission

- **Per-request trigger:** a word the user chooses ("look", say) captures the game window together with what was said, and sends both to the brain.
- **Standing permission (off by default):** a setting that lets Ultimo capture whenever the state tracker needs to confirm something.
- Only the game window is captured, never the whole desktop.
- Captures live in memory and are discarded after use, unless the user says "save that".
- An indicator in the control panel and a short sound show when a capture happens.

Vision is never on the fast path. It's for questions ("what's this contract worth?"), confirmations, and reading things the log doesn't record. **(to measure)** How long a capture plus answer takes.

### 4.5 Keybinds and actions

- Ultimo reads the user's bindings from Star Citizen's `actionmaps.xml`, under `LIVE\user\client\0\Profiles\default\`.
- It turns them into actions with friendly names and the phrases that trigger them. The user can edit the list.
- Sequences ("startup", "prepare to land") are named, editable lists of actions with waits between them.
- When an action has no binding, Ultimo says so instead of guessing.
- After a patch, it rereads the file and reports actions that were added, removed or changed.

### 4.6 The sorter

The sorter must be fast and must say when it isn't sure. It starts as a small, fast Claude model. Decision models such as TypeSafe's Jev can be tested against it on the same test set. Others have reported Jev dropping sentences, so it starts as a candidate, not the default.

### 4.7 Safety and confirmations

Every action passes through one safety gate, which can refuse it.

- Actions are graded:
  - **Free:** gear, lights, doors.
  - **Announce:** quantum jump, power off.
  - **Confirm:** self-destruct, eject, accepting or abandoning a contract, purchases.

  Users can move actions between grades, but nothing leaves the confirm grade without a deliberate settings change.
- Confirmation is by voice ("confirm"), and the request expires after a few seconds.
- A spoken "stop" or "cancel", or pressing push-to-talk, immediately halts any sequence and releases every held key.
- Every action is logged with what was pressed and why, so the user can review it and say "undo that" where possible.
- If the internet or an outside service fails, the fast path keeps working, and Ultimo says plainly that the rest is unavailable instead of hanging.

### 4.8 Persona and voice

The persona is configurable: name, voice, tone and how chatty it is. It keeps replies short in combat and talks more when idle. There's one default persona, kept as an editable text file.

The default draws on the lore. In Star Citizen's universe, humanity distrusts AI after Janus, the AI core blamed for the loss of the *Artemis*. Ultimo's default persona knows this. It's careful, a little defensive about not being Janus, and always reads back what it's about to do.

## 5. Delivery stages

0. **Latency spike.** Measure the candidate stacks with the game running before committing to the architecture. See [ADR 2](adr/0002-cascaded-voice-pipeline.md).
1. **Hands on the controls.** Push-to-talk, transcription, keybind import, the fast path, the brain with key tools, voice, the safety gate and the action log. Done when startup, landing and quantum sequences work by voice.
2. **Eyes.** The vision trigger, state tracker, log reader and check-before-toggle. Done when "turn on shields" never turns them off in the test set.
3. **Knowledge.** UEX Corp, the Wiki API and fleet memory. Done when trade, loadout and location questions are answered with a cited source.
4. **Polish.** Persona, standing vision permission, control panel, sequence editor and patch-change reports.

Settings (one schema, voice and file editing) arrive in stage 1. The settings GUI and conversational onboarding follow once the voice pipeline is proven.

## 6. How we'll know it's world-class

- **Test set:** at least 200 recorded phrases, each with the right outcome, including loose wording, chained commands and noise. Run on every change.
- **Accuracy:** the right action, or a correct "I'm not sure", on at least 95% of the test set. Zero wrong actions on confirm-grade commands.
- **Speed, from end of speech to action** (targets, not measurements):
  - under 300 ms on the fast path;
  - under 1.5 s when thinking without vision;
  - under 4 s with vision.
- **Hands-off test:** a full session (startup, travel, a mission, landing) without touching the keyboard for any action Ultimo supports.
- **Game impact:** a measured, published frame-rate cost.
- **Cost:** a published cost per hour of play for each provider setup, including a free or local one.

## 7. Open questions

1. Which default voice and persona tone tests best over long sessions?
2. Should Ultimo ever speak unprompted, for example to warn about low fuel? If so, how do we keep it from becoming noise?
3. How much does `Game.log` record in the current patch? This sets how much the state tracker must rely on vision.
4. Can local speech-to-text run alongside the game without a noticeable frame-rate cost? **(to measure)**
5. What will CIG say when asked directly about a user-triggered voice keybind tool? See [ADR 3](adr/0003-staying-within-cig-rules.md).
