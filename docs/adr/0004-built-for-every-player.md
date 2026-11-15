# 4. Built for every player, modular at the seams

Date: 2026-10-30 · Status: accepted; privacy amended 2026-11-14 for opt-in crash reports (see the end)

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

**Private by default.** No telemetry. Diagnostics are opt-in bundles the user reviews before sharing. *Amended 14 November 2026: crash reports may be sent, only if the player says yes (see the end).*

## Consequences

- More setup UX to build: a binding wizard and install detection.
- Every adapter needs a fake and contract tests, which keeps CI hardware-free.

## Amendment: opt-in crash reports (14 November 2026)

**Context.** Until now Tycho has had one user, its author, so every crash was seen. From 0.8 other players install it ([#199](https://github.com/benshapyro/tycho-ai/issues/199)), and a crash on their PC is seen only if they report it. Most won't. Local crash records ([#296](https://github.com/benshapyro/tycho-ai/issues/296)) help a player who sends them in, but not one who just uninstalls. "No telemetry" above rules out any report leaving the PC, even with the player's consent. The maintainer agreed on 14 November 2026 to allow crash reports on the terms below, and to leave usage counts out for now.

**Decision.** "No telemetry" becomes **"nothing leaves the PC unless the player says yes"**, and the only thing they can say yes to is crash reports.

- **Off by default.** Nothing is sent until the player agrees. A player who never answers sends nothing.
- **Asked once, at setup.** One plain question, such as "Send crash reports to help fix Tycho?", with what is sent and what never is, next to it. No and yes are equal choices; neither is picked for them. Players who set Tycho up before this ships are asked once, on the next start.
- **Changeable any time** in the app's settings and in `settings.toml` (a setting such as `crash_reports`).
- **By voice, only towards off** (`lower_only`, ADR 5): "stop sending crash reports" works; nothing said turns them on. The same rule as transcripts, web search and key actions: a misheard sentence must never loosen privacy.
- **What a report holds:**
  - the error's type and where it happened in Tycho's own code (module, function and line);
  - Tycho's version, the Windows version, the game pack's id, and which brain provider is set (not the model's replies);
  - whether it was a Python error or a native crash found on the next start (#296).
- **Never sent:**
  - transcripts or anything the player or Tycho said, and the brain's prompts, replies or tool calls;
  - API keys, or anything the key redaction would catch;
  - the player's name, what Tycho calls them, and their Windows user name;
  - file paths outside Tycho's own code (the player's folders, the game folder);
  - local variables, log lines, settings values and any lasting ID for the player or the PC. The receiving service keeps no IP address.
- **Scrubbed before it leaves.** Every report passes one scrubber in Tycho before sending, and tests check each "never sent" item against it with planted values. If the scrubber fails, the report is dropped, not sent raw.
- **Turned off means off.** A report waiting to be sent is deleted when the player says no, and nothing is sent from a run that started with reports off.
- **Usage counts are not part of this.** Counting features or setup steps (#199's analytics) needs its own amendment and the maintainer's yes, likely with the business model (#195).
- **The service is Sentry** (the maintainer's choice, 14 November 2026), on Tycho's own Sentry project. Its SDK (MIT) is set so it sends nothing on its own: no default personal data, breadcrumbs, sessions, performance traces or IP. If Sentry's terms or price stop fitting, a self-hosted GlitchTip takes the same reports, under these same rules.
- **Diagnostics bundles stay as they are:** the player reviews them before sharing.

**Consequences.**
- One more setting, one more setup question (#364, onboarding), and a short "what's sent" page the question links to.
- The README and the setup question must list exactly what is sent; a change to that list is a change to this ADR.
- A new outside service, called only when the player says yes, and only after a crash. Tycho still works fully with it off or unreachable.
- The update check (#9) is unchanged: it asks GitHub for the latest version and sends nothing about the player.
