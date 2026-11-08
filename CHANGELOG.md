# Changelog

What changed in each version of Tycho, for players. The newest version is first.

Each version's full notes, with examples, are on its [GitHub release](https://github.com/benshapyro/tycho-ai/releases). The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/): while Tycho is 0.x, the second number goes up for new features and the third for fixes only.

**Adding to it:** a PR that changes something a player can notice adds a line under **Unreleased**, in plain words, with its PR or issue. Fixes to work that hasn't been released yet, and work behind the scenes, don't need a line. The release moves **Unreleased** under the new version's number and date (see the `release` skill).

## [Unreleased]

<!-- A full draft of the 0.5.0 release notes, with examples, is in git:
     `git show 24b13cf:docs/releases/0.5.0.md`. Start the release notes from it. -->

### Changed
- **Check after upgrading if you edit `commands.toml` by hand:** a `requires` or `accepted_risks` value Tycho doesn't know, such as a typo, now stops the whole file loading. Until you fix it, Tycho uses only its built-in commands, and the Commands page says so. `tycho check` names the value and the ones allowed: correct or remove it, then run `tycho check` again. Before, the typo was ignored and that command was always refused. ([#206](https://github.com/benshapyro/tycho-ai/pull/206), [#218](https://github.com/benshapyro/tycho-ai/issues/218))
- A trade route asked from where you are ("I'm at Obituary, best route for refined gold?") is one small request to UEX instead of two, and a slow shop is left out instead of holding up the answer. ([#211](https://github.com/benshapyro/tycho-ai/pull/211))
- Asked about the wrong kind of thing by name, Tycho checks the Star Citizen Wiki's search: "Cutlass Black" is the ship, not the plushie, and "the Gallant" as a ship finds the Gallant rifle. Weapons named after a ship, like the "Mantis gatling", stay weapons. ([#220](https://github.com/benshapyro/tycho-ai/pull/220))
- Tycho checks the drive has room before it downloads the speech model, and says how much it needs, how much is free, and where. ([#203](https://github.com/benshapyro/tycho-ai/pull/203))

### Fixed
- When the graphics card runs short mid-request, Tycho plays the "not done" sound at once instead of acting on what you said about 20 seconds later, and loads the processor copy in the background. It also frees the card's memory first. ([#209](https://github.com/benshapyro/tycho-ai/pull/209))
- If the switch to the processor fails, Tycho tries again 30 seconds later, then waits longer between tries (up to 5 minutes), instead of staying without speech recognition until a restart. ([#215](https://github.com/benshapyro/tycho-ai/pull/215))
- Since 0.4, an answer that needed a look-up could run out of time while it was still coming in. Each look-up gives it more time again. ([#212](https://github.com/benshapyro/tycho-ai/pull/212))
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

[Unreleased]: https://github.com/benshapyro/tycho-ai/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.4.0
[0.3.3]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.3
[0.3.2]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.2
[0.3.1]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.1
[0.3.0]: https://github.com/benshapyro/tycho-ai/releases/tag/v0.3.0
