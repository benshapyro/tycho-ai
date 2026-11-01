# 11. Runtime architecture

Date: 2026-10-30 · Status: accepted; roadmap amended by [ADR 14](0014-the-brain-voice-and-sequences.md)

> **As built (2026-11-01):** the brain and the voice shipped together in 0.3 (ADR 14), not in 0.3 and 0.4 as below. The stages run inside one `Session` method rather than as separate replaceable stages; splitting it is tracked as an issue.

## Context

Every part works on its own and is tested:
- push-to-talk and microphone capture;
- Parakeet transcription (benchmarked);
- the game-term lexicon;
- the fast-path matcher;
- keybinds from the game's files;
- the hands, tested in game.

Release 0.1 joins them into one program: hold push-to-talk, speak, Tycho presses the right keys. Running continuously on players' PCs adds what isolated parts don't have: threads, devices that disappear, a game that loses focus, long sessions, and shutdowns that must never leave a key held down. These decisions are hard to change later, so they're made before the code.

## Decision

### Process and concurrency
- **One process, and only one instance at a time.** A named Windows mutex stops two copies from fighting over the keyboard.
- **An asyncio event loop coordinates everything.** Nothing that blocks runs on it. Blocking work has its own thread and hands results back with `loop.call_soon_threadsafe`:
  - the audio callback (PortAudio's own thread);
  - push-to-talk polling (every 5 ms);
  - transcription (one worker);
  - key presses (one worker, so one press at a time).
- **Each resource has one owner.** One component owns the microphone, one owns the keyboard.

### A pipeline of stages with typed messages
Each stage is a small, replaceable unit behind a `typing.Protocol` (ADR 4), passing immutable messages:

`PushToTalk pressed/released` → `Clip` → `Transcript` → `Corrected` (lexicon) → `Decision` (fast path, later the brain) → **`Verdict` (safety gate)** → `Press` plan → `Receipt` → feedback.

- **Every request has an ID and a monotonic timestamp per stage**, so each one logs a timing line ("gear down: 112 ms; transcribe 95, match 6, press 11"). This is how the under-300 ms target (architecture section 6) is proven in real play.
- **One request at a time.** Pressing push-to-talk again cancels whatever is in flight. A key sequence stops within about 10 ms and releases every key (ADR 10).
- **The safety gate is its own stage from 0.1 on**, even while it's minimal. In 0.1 it refuses confirm-grade actions outright. Spoken confirmation arrives in 0.2 ([#19](https://github.com/benshapyro/tycho-ai/issues/19)). No other stage can reach the hands.

### Behaviour the player sees (decided with the author)
- **Hold to talk.** Hold the button while speaking; release to send. Tap-to-toggle may come later as a setting.
- **Feedback is two short sounds until Tycho can speak (0.4).** A click for "done", a low tone for "not done": didn't understand, refused, no key, or the game not focused. Tycho generates the sounds itself, so there are no asset licences. They play without blocking, at a volume set in settings.
- **If the game isn't in front, listen but don't press.** Transcribe as usual, never send keys, play the "not done" sound, and log "game not focused". Commands are never queued for later.
- **Push-to-talk conflicts are checked.** If the chosen button is also bound in game, Tycho warns: it can see the press but can't stop the game seeing it too.

### Transcription
- **Parakeet TDT 0.6B v2, through onnx-asr and ONNX Runtime.**
- **The device is chosen automatically:** the GPU when CUDA is available with enough free graphics memory, otherwise the CPU. A setting overrides it. The default gets revisited after the frame-rate test ([#23](https://github.com/benshapyro/tycho-ai/issues/23)): the GPU session reserved about 3.5 GB of graphics memory with Star Citizen already using about 8 GB.
- **Tycho manages the model (ADR 6).** It downloads a pinned version once, checks its SHA-256, and keeps it in `%LOCALAPPDATA%\tycho\models`. After that it works offline, loads the model once and warms it up at startup.

### Settings, paths and logging
- **One `tycho.paths` module**, the only place file locations are decided:
  - settings in `%APPDATA%\tycho\settings.toml`;
  - caches, models and logs under `%LOCALAPPDATA%\tycho`.
- **Settings are a typed, validated schema (ADR 5)** saved as TOML. The schema can produce a JSON Schema, which the settings GUI will be generated from.
  - 0.1 settings: the push-to-talk button, microphone, game folder, transcription device, feedback volume, and whether to keep transcripts.
  - Keeping transcripts is off by default.
- **Logging uses stdlib `logging`.**
  - A readable console log, plus a rotating JSON-lines file in `%LOCALAPPDATA%\tycho\logs`.
  - A redaction filter guarantees API keys never reach a log, and a test checks it.
  - Transcripts are logged only if the player turns it on.
- **No telemetry** (ADR 4).

### Failure and shutdown
- **Every expected failure has a defined behaviour and a plain-language message** ([#11](https://github.com/benshapyro/tycho-ai/issues/11)):
  - a missing or muted microphone;
  - no game install;
  - unreadable keybinds;
  - a failed model download;
  - the game not focused;
  - an action with no key.

  The loop keeps running through any single request's failure.
- **Shutdown is safe.** On Ctrl+C, closing the window, or an unhandled error, Tycho cancels in-flight work, releases every held key, closes the microphone and flushes logs.

### Commands
- `tycho run` starts the loop.
- `tycho check` runs a diagnosis:
  - microphone level;
  - game found;
  - keybinds loaded;
  - model ready, and on which device;
  - push-to-talk conflicts;
  - the focus check.

### Testing
| Layer | Proves | Runs |
|---|---|---|
| Unit tests with fakes | Each stage's rules | Every commit (CI) |
| Pipeline test: fake microphone and push-to-talk, real lexicon and matcher, fake keyboard | The whole loop: ordering, cancellation, focus, shutdown | Every commit (CI) |
| Replay test: recorded clips, real Parakeet, fake keyboard | Real recognition end to end, with timings | On demand and before releases (needs the model and local recordings) |
| In-game checklist | It works in Star Citizen | Before each release, recorded in the release notes |

### Releases
Releases ship as usable slices, each with a version, a changelog entry and done criteria:
- **0.1:** the talking loop with the fast path only.
- **0.2:** the safety gate and confirmations, and the action log.
- **0.3:** Claude for everything else.
- **0.4:** spoken replies.

## Consequences

- **New runtime dependencies:** onnx-asr and onnxruntime-gpu (which also runs on the CPU) for transcription, and pydantic for settings. Their licences are checked in [#6](https://github.com/benshapyro/tycho-ai/issues/6). GPU support makes a large download, which matters for packaging ([#9](https://github.com/benshapyro/tycho-ai/issues/9)).
- **The bench tools stay separate** and keep measuring the same stages, so a regression shows up in both.
- **Later releases add stages, not rewrites:** the router and brain slot in at `Decision`, voice at feedback, and the log reader and vision feed a state stage the gate consults.
