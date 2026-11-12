# Changelog

What changed in each version of Tycho, for players. The newest version is first.

Each version's full notes, with examples, are on its [GitHub release](https://github.com/benshapyro/tycho-ai/releases). The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/): while Tycho is 0.x, the second number goes up for new features and the third for fixes only.

**Adding to it:** a PR that changes something a player can notice adds a line under **Unreleased**, in plain words, with its PR or issue. Fixes to work that hasn't been released yet, and work behind the scenes, don't need a line. The release moves **Unreleased** under the new version's number and date (see the `release` skill).

## [Unreleased]

### Added
- Run Tycho's brain through OpenRouter instead of Anthropic, with one OpenRouter key: set `brain_provider = "openrouter"` in Tycho's window (Settings, Brain) or in `settings.toml`, and store the key with `tycho key set openrouter` or on the Keys page. It's the same Claude Haiku as before; only models that passed Tycho's test with no wrong key presses can be picked, and confirm-grade actions still ask first. Web search still needs an Anthropic key: without one, Tycho tells you it can't search instead of failing quietly. `tycho check` names the provider and asks it (free) whether your key works. Claude direct stays the default, so nothing changes unless you pick it. ([#112](https://github.com/benshapyro/tycho-ai/issues/112))
- Tycho's window shows which game it helps with (Settings, Game and controls), and `tycho check` names it. Star Citizen is the only game for now, so nothing changes. Your commands and fleet files stay where they are. ([#293](https://github.com/benshapyro/tycho-ai/issues/293))
- Turn key actions off to use Tycho as an advisor only: set `key_actions` off in Tycho's window (Settings, Safety) or in `settings.toml`. Tycho then answers, looks things up and shows cards, and presses nothing: no command, sequence, Claude pick or "confirm" gets a key pressed. By voice it can only be turned off, never on. A game whose rules forbid tools that press keys will always be advisor only. `tycho check` and the window's status say whether key actions are on, and why not. When the game isn't the window in front, Tycho now names the game that isn't focused. ([#191](https://github.com/benshapyro/tycho-ai/issues/191))
- Tycho's window lists each game under Settings, Game and controls, with whether Tycho presses keys there and, if not, why. Picking another game restarts Tycho with it at once, and its window opens again. The Commands page shows the chosen game's commands, and says when key actions are off. Star Citizen is the only game for now. ([#295](https://github.com/benshapyro/tycho-ai/issues/295))
- World of Warcraft, as an advisor: pick it under Settings, Game and controls, and Tycho answers questions about the game (classes, dungeons, professions, Midnight), explains how things work, and shows cards, by voice. It presses no keys there, since Blizzard's rules don't allow tools that play the game for you, and it says so if you ask it to do something. It looks nothing up yet: answers come from what Claude knows, plus a few dated facts about the current patch. ([#300](https://github.com/benshapyro/tycho-ai/issues/300))
- A trial, off by default: set `web_search_open = true` (Settings, Privacy, or `settings.toml`) and web search may use any website, not only Star Citizen ones, and read up to two pages it finds, so it can answer "where is" and "how do I" questions from community guides and forums. Answers from players say so, and a search can take up to about 25 seconds and costs about 1 to 3 cents. ([#354](https://github.com/benshapyro/tycho-ai/issues/354))
- `brain_seconds` sets how long Tycho waits for the brain before saying it couldn't answer (8 seconds, as before). ([#112](https://github.com/benshapyro/tycho-ai/issues/112))

## [0.6.0] - 2026-11-12

### Changed
- Tycho guesses less. A request it can't place ("faster", "same as before") or one that could mean something drastic ("kill it") now gets a one-line question instead of a press. What you say to your squad on the same push-to-talk ("break right", "form up on me") presses nothing, not even shields or targeting, and gets a plain "Copy.", while "look behind" still turns your view. "Take us to Crusader" opens the star map (if it has a key) and tells you to set the destination there, since Tycho can't choose where quantum goes, and "open fire" says the trigger is yours instead of powering weapons. ([#271](https://github.com/benshapyro/tycho-ai/issues/271))

### Fixed
- Chat that isn't a request ("good fight everyone, o7") sometimes got an error instead of Tycho's reply. Now you hear the reply, without Tycho explaining why it pressed nothing ("not a request for me, so…"). ([#278](https://github.com/benshapyro/tycho-ai/pull/278))
- Asking for a long how-to or plan ("teach me how to salvage") could end in the error sound, because the card ran past the length Claude may answer in. Tycho now has twice the room, asks Claude to keep cards to what the Tile shows, and if an answer still runs too long it says so ("Ask me for part of it") instead of showing half a card. ([#266](https://github.com/benshapyro/tycho-ai/issues/266))

## [0.5.0] - 2026-11-09

### Added
- Missed what Tycho said? "Say that again" (or "repeat that", "come again") repeats the last thing it said word for word, an answer or what it just did ("Lights."), at once and as often as you like, with the Tile on or off. Asked while Tycho waits for you to confirm, it asks the question again and gives you the full time to answer. It only repeats the words: it never presses a key again. Before, it went back to Claude, took a second or two, might come back worded differently, and stopped working after half a minute. ([#225](https://github.com/benshapyro/tycho-ai/pull/225), [#222](https://github.com/benshapyro/tycho-ai/issues/222), [#226](https://github.com/benshapyro/tycho-ai/issues/226))

- Choose Tycho's voice with the new `voice` setting, in Tycho's window (Settings, Voice and sounds) or in `settings.toml`: George, Sarah, Lily, Brian, or any voice ID from your ElevenLabs account, such as one you designed. Each voice uses its own model (George is on Flash v2.5, the quickest; the others on v4 Turbo), and `voice_model` can pick the other. It applies when Tycho next starts. At start, Tycho asks ElevenLabs whether your key can use the voice (free, no characters used). If it can't, Tycho speaks as George and says so on the Tile, in its window and in the log, instead of paying for a failed first line. `tycho check` names the voice and model and warns if yours isn't available. ([#242](https://github.com/benshapyro/tycho-ai/issues/242))

### Changed
- **The default voice is now George on Flash v2.5,** which answers about 0.1 s sooner. Before, the default was The Ship's Mind, a designed voice only its maker's account can use, so every other key got George on v4 Turbo after a failed first request. If you made The Ship's Mind or another voice of your own, set `voice` to it. ([#242](https://github.com/benshapyro/tycho-ai/issues/242), [ADR 9](docs/adr/0009-voice.md))
- **Check after upgrading if you edit `commands.toml` by hand:** a `requires` or `accepted_risks` value Tycho doesn't know, such as a typo, now stops the whole file loading. Until you fix it, Tycho uses only its built-in commands, and the Commands page says so. `tycho check` names the value and the ones allowed: correct or remove it, then run `tycho check` again. Before, the typo was ignored and that command was always refused. ([#206](https://github.com/benshapyro/tycho-ai/pull/206), [#218](https://github.com/benshapyro/tycho-ai/issues/218))
- A trade route asked from where you are ("I'm at Obituary, best route for refined gold?") is one small request to UEX instead of two, and a slow shop is left out instead of holding up the answer. A list UEX sends too slowly for one answer is kept for the next time you ask, instead of failing every time. ([#211](https://github.com/benshapyro/tycho-ai/pull/211), [#231](https://github.com/benshapyro/tycho-ai/issues/231))
- When a price, route, mining or item source hangs, Tycho gives up on it sooner (after 3 to 5 seconds, down from 4 to 6) and answers without it, so a slow site no longer uses up the time Claude needs to reply. Every look-up answered in under a second when timed with the game running. ([#230](https://github.com/benshapyro/tycho-ai/pull/230))
- Asked about the wrong kind of thing by name, Tycho checks the Star Citizen Wiki's search: "Cutlass Black" is the ship, not the plushie, and "the Gallant" as a ship finds the Gallant rifle. Weapons named after a ship, like the "Mantis gatling", stay weapons, and the kind of item you name counts: "Arrow pistol" isn't taken for the Arrowhead sniper rifle, nor "Gladius cannon" for a model of the Gladius. ([#220](https://github.com/benshapyro/tycho-ai/pull/220), [#233](https://github.com/benshapyro/tycho-ai/issues/233))
- A phrase of yours that is one of Tycho's own words ("repeat that", "confirm", "clear the Tile") never reached your command, because Tycho answers those first. The Commands page now refuses it, and `tycho check` warns about one already in your `commands.toml` and says what Tycho uses it for. ([#227](https://github.com/benshapyro/tycho-ai/issues/227))
- Tycho checks the drive has room before it downloads the speech model, and says how much it needs, how much is free, and where. ([#203](https://github.com/benshapyro/tycho-ai/pull/203))

### Fixed
- When the graphics card runs short mid-request, Tycho plays the "not done" sound at once instead of acting on what you said about 20 seconds later, and loads the processor copy in the background. It also frees the card's memory first. ([#209](https://github.com/benshapyro/tycho-ai/pull/209))
- If the switch to the processor fails, Tycho tries again 30 seconds later, then waits longer between tries (up to 5 minutes), instead of staying without speech recognition until a restart. ([#215](https://github.com/benshapyro/tycho-ai/pull/215))
- Since 0.4, an answer that needed a look-up could run out of time while it was still coming in. Each look-up gives it more time again. ([#212](https://github.com/benshapyro/tycho-ai/pull/212))
- An Anthropic key stored with a hidden character, such as a zero-width space pasted from a web page, no longer reaches Claude and fails there. Tycho treats it as no key, says so in the log and in `tycho check`, and asks you to store it again, as it already does for an ElevenLabs key. `tycho key set` and Tycho's window refuse such a key. ([#248](https://github.com/benshapyro/tycho-ai/issues/248))
- A key stored with a hidden character now shows as **Unusable** on the Keys page of Tycho's window, with a note to remove it and paste it again, instead of "Stored". `tycho check` says the key is stored but unusable, rather than "no key", and `tycho key check` says the same. This is true for both the Anthropic and ElevenLabs keys. ([#250](https://github.com/benshapyro/tycho-ai/issues/250))
- Sounds no longer hold Tycho up, the first request after a keybind change is as quick as any other, and quitting shuts down cleanly. ([#207](https://github.com/benshapyro/tycho-ai/pull/207))

### Security
- Tycho won't press a key combination when part of it is, on its own, an action you'd have to confirm (Lights on right Alt + X, with Eject on right Alt alone), and the refusal names the key that clashes. ([#206](https://github.com/benshapyro/tycho-ai/pull/206), [#217](https://github.com/benshapyro/tycho-ai/pull/217))

## [0.4.0] - 2026-11-07

### Added
- Tycho's own window, opened from the Start menu or the star by the clock: **Status**, **Settings** (with **Undo**) and **Commands**. Right-click the star to pause listening, show or hide the Tile, open the logs, or quit.
- A guided first run: find the game, choose the push-to-talk button, check the microphone, add your keys.
- The Tile: a small see-through panel over the game for details better read than heard, like prices and routes. Its colour follows your ship's maker, and you can move, resize or hide it by voice. It needs Borderless window mode.
- New look-ups: where to mine a resource, where to refine an ore, and where to buy a ship with aUEC.
- "Checking." while Tycho looks something up, kept on your PC after the first time so it doesn't use voice credits.
- Settings by voice ("speak a bit quieter", "undo that"), except anything that would make Tycho less careful.
- Tycho calls you what you like: "Commander", "Captain", your name, or nothing.
- Your own phrases for any command, from the Commands page.

### Changed
- Trade answers work without saying where you are, and understand "refined gold", "gold ore" and whole systems like Pyro.
- A command and your ship in one go ("power off, I'm in my Pitbull") does both.
- Prices and routes from UEX always say so.

### Fixed
- Item names said as letters ("S F nine") are found.
- Tycho never reads out the instructions meant for the Tile, and picks again if it chose a key you don't have.

## [0.3.3] - 2026-11-04

### Changed
- The speech model takes about 3.0 GB of graphics memory instead of 3.5 GB, at the same speed. Tycho uses the card when 4.0 GB is free (it needed 4.5 GB).
- Answers from Claude arrive about a quarter of a second sooner.
- Long sentences are matched against the built-in commands in under a millisecond (up to 40 ms before).
- After pressing keys, Tycho says what it sent ("Max power to engines"), not a result it can't check.
- The log shows when each key went down.

### Fixed
- A command with no key bound gets the action to bind, instead of a press of something nearby.

## [0.3.2] - 2026-11-04

### Fixed
- Starting Tycho while Star Citizen holds most of the graphics card's memory no longer stops with an error: speech recognition switches to the processor, at the start or later in a session.

## [0.3.1] - 2026-11-04

### Added
- Setup by an AI agent (Claude Code, Codex, Cursor and others), which opens a window whenever it needs you and never sees your keys.
- `tycho check --json`, `tycho model download` and `tycho model status`, and `tycho actions <words>` to find the game's action names.

### Security
- `tycho key set` won't read a key piped in by a script or agent, so a key never ends up in a transcript.

## [0.3.0] - 2026-11-04

The first release with an installer, as a preview. Earlier versions weren't released.

### Added
- Built-in commands matched on your PC and pressed instantly, through your own keybinds; Claude for anything said differently; your own phrases and sequences in `commands.toml`.
- Keys pressed only while Star Citizen is in front, "confirm" before anything you can't undo, and push-to-talk stops everything.
- Answers about ships, prices and trade routes (UEX), weapons, armor, components and crafting, and news.
- A fleet Tycho remembers, and speech recognition on your PC.
- A check for a newer version when Tycho starts (it never updates itself).

[Unreleased]: https://github.com/benshapyro/tycho-ai/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.6.0
[0.5.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.5.0
[0.4.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.4.0
[0.3.3]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.3
[0.3.2]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.2
[0.3.1]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.1
[0.3.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.0
