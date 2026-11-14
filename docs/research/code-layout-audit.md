# Code layout audit: modules, sizes and coupling

Audited 2026-11-14, on `main` at 0cc5be9 (0.7.0), for [#362](https://github.com/benshapyro/tycho-ai/issues/362) (refactor the app end to end) and what's left of [#61](https://github.com/benshapyro/tycho-ai/issues/61) (split `app.py`). The decisions it leads to are in [ADR 19](../adr/0019-code-layout.md) (proposed).

This is a reference doc, not a spec. Everything here was read from the code. It follows the [modularity audit](modularity-audit.md) of 11 November, which settled game packs (ADR 18); this one looks at how the code is laid out now that packs exist.

## Summary

- **`src/tycho` is 111 Python files and about 21,000 lines.** Most modules are small and have one job. Nine are over 500 lines; three of them hold most of the tangle: `app.py` (1,508), `runtime/session.py` (847) and `desktop/bridge.py` (709).
- **`app.py` is nine jobs in one file**: the command line, seven commands, the start and restart loop, the desktop host, the session wiring, the voice and speech loaders, `tycho check`'s checks, Star Citizen's install questions, and push-to-talk helpers. The desktop imports it back for five of those helpers, so `app` and `desktop` import each other.
- **Packs name their parts by id, but nothing builds a part from its id.** `packs/parts.py` holds the ids only. `app.py` and `brain_choice.py` then build each Star Citizen part by name, behind `if "ships" in wanted` and `if pack.parts.keybinds is not None`. Adding a part means editing both.
- **"No pack given" means Star Citizen in 13 files**, including the safety gate. `safety.check(..., lists=None)` uses Star Citizen's lists, and those include lists that loosen a check (`mode_guards`, `accepted_risks`). Today every running path passes the pack's lists (`build_session` does), so this isn't a live bug. It's a trap for the next caller: a second game with key actions that forgot to pass its lists would get Star Citizen's loosening lists, and nothing would fail.
- **Three modules load the Star Citizen pack when they're imported** (`safety`, `tile/design`, `knowledge/web`), so importing them reads `packs/star-citizen/`.
- **The general keybind types live in Star Citizen's folder.** `Action`, `KeyInput`, `ActivationMode` and `Keybinds` are in `game/keybinds.py`, next to the `Data.p4k` parser. Ten modules outside `game/` import them, which is why `game/` looks like everyone's dependency.
- **25 files outside the pack name Star Citizen; 5 import its code.** 4 name it in logic, 13 by defaulting to it when no pack is given, 1 by design (the parts registry), and 7 only in comments or the product's own description. (The 0.8 plan's 13 was an older, narrower count.) Five modules import Star Citizen-only code: `app.py`, `brain_choice.py`, `desktop/setup.py`, `desktop/bridge.py` and `settings.py`.
- **Windows DLLs are loaded in seven modules** (#298 makes it one).

## Part 1: the modules

Lines per top-level package or module, and what each imports from the rest of Tycho. "Fan-in" is how many other packages import it.

| Unit | Lines | Imports from Tycho | Fan-in | Job |
|---|---|---|---|---|
| `knowledge/` | 3,691 | brain, packs, text | 2 | Look-ups: the Wiki, UEX, ships, fleet, items, mining, refining, trade, and web search |
| `brain/` | 2,258 | commands, game, input, logs, packs, paths, safety, text, tile | 6 | The brain: briefing, Claude and OpenRouter adapters, the tool loop, traces |
| `desktop/` | 2,125 | **app**, address, audio, commands, credentials, game, packs, paths, ptt, runtime, safety, settings, text, tile, updates, win32 | 2 | Tray, window, page, bridge, setup, Commands page, host |
| `runtime/` | 1,831 | audio, brain, commands, game, input, lexicon, logs, packs, paths, ptt, safety, text, tile, voice | 5 | The talking loop and its pure parts |
| `tile/` | 1,614 | packs, settings, win32 | 6 | The Tile |
| `app.py` | 1,508 | 24 of the 28 others | 2 | See Part 2 |
| `game/` | 1,162 | paths | **10** | Star Citizen's files: install, `Data.p4k`, CryXmlB, keybinds, `Game.log`, the layout tool |
| `stt/` | 941 | audio, runtime | 1 | Speech to text |
| `commands/` | 660 | game, lexicon, packs, safety, text | 6 | The fast-path matcher and command files |
| `voice/` | 585 | text | 3 | ElevenLabs and playback |
| `packs/` | 571 | game | **11** | The pack loader and the parts registry |
| `input/` | 565 | game | 5 | Key codes, press planning, the presser, the Windows sender |
| `settings.py` | 505 | brain, game, paths, ptt, tile, voice | 6 | The settings schema |
| everything else | about 3,000 | | | `audio/`, `ptt/`, `lexicon/`, `safety.py`, `spoken_settings.py`, `brain_choice.py`, `win32.py`, `credentials.py`, `address.py`, `feedback.py`, `logs.py`, `updates.py`, `text.py`, `paths.py`, `findings.py`, `eval/` |

### Files over 500 lines

| File | Lines | Why it's long |
|---|---|---|
| `app.py` | 1,508 | Nine jobs (Part 2) |
| `runtime/session.py` | 847 | `Session` joins every stage; `_process` alone is 140 lines, and speaking, the Tile and confirm questions are methods on it |
| `desktop/bridge.py` | 709 | One class for every page: settings, keys, updates, setup, Commands, and the Tile preview |
| `tile/draw.py` | 622 | One drawing function per card kind |
| `brain/openrouter.py` | 606 | The adapter plus its own stream parsing |
| `knowledge/items.py` | 566 | Items and crafting from the Wiki |
| `commands/matcher.py` | 537 | Matching, loading, merging the player's file, and checking it |
| `knowledge/ships.py` | 519 | Ship data and name matching |
| `settings.py` | 505 | The schema: one entry per setting, with its description |

The next five are 400 to 475 lines (`knowledge/web.py`, `packs/pack.py`, `desktop/commands.py`, `brain/rounds.py`, `knowledge/trade_lookup.py`).

### Import knots

- **`app` ↔ `desktop`.** `app.py` starts the desktop; `desktop/setup.py` and `desktop/bridge.py` import `find_install`, `input_sources`, `describe`, `conflict_warnings` and `microphone_device` back from `app` (lazily, to dodge the loop). Those helpers aren't the app's: four are push-to-talk and microphone helpers, one is Star Citizen's install.
- **`brain_choice` → `desktop`.** Choosing the brain imports `desktop.keys` for the key check.
- **`knowledge` → `brain`.** Every look-up imports `brain.lookups` for `LookupResult`. That's the right direction (the brain owns the `Lookup` boundary); it's listed so nobody "fixes" it.
- **`settings` → `brain`, `tile`, `voice`, `ptt`.** The schema imports the lists its choices come from (models, card kinds, voices, the binding type). It makes `settings` a high-level module, but the alternative (copying the lists) is worse. Left as is.

## Part 2: what's in `app.py`

| Lines | Job | Who else needs it |
|---|---|---|
| 63-166 | The command line: `main`, the parser, dispatch, `credits` | Nobody |
| 172-288 | `tycho run`, the start and restart loop (#295, #329) | Nobody |
| 289-402 | `_start`: builds the speech model, keybinds, voice, Tile, microphone and session | The tests |
| 403-619 | The desktop host: the tray, the app window, the "starting" star, first-run setup, Ctrl+C | Only the desktop |
| 620-730 | The pack, keybinds and speech model loaders, and their findings | `tycho check`, the bridge |
| 732-845 | `actions`, `model` and `gpu` commands | Nobody |
| 845-924 | The voice: the key, the choice, the speaker | `tycho check` |
| 925-1085 | Session wiring: commands, `build_session`, `serve`, keybind refresh | The tests, the eval's harness |
| 1086-1359 | `tycho check`: every check, printed or as JSON | The window's status page |
| 1360-1397 | `bind` and `sounds` | Nobody |
| 1398-1422 | Star Citizen's install: find it, ask for it | **The desktop** |
| 1423-1508 | Microphone and push-to-talk helpers | **The desktop** |

Tests patch `app` functions in 63 places across five `test_app_*.py` files (2,593 lines). `pyproject.toml`'s entry point is `tycho.app:main`, and `packaging/tycho_main.py` and `tycho.spec` list lazily imported modules by name.

## Part 3: Star Citizen outside its pack

The 25 files outside `game/`, `knowledge/` and `packs/star-citizen/` that name Star Citizen (a search for "Star Citizen", `StarCitizen` and `star-citizen`), by kind. One more (`desktop/bridge.py`) doesn't name it but imports its code: see the last table.

### In logic (4 files)

| File | What |
|---|---|
| `app.py` | `tycho check`'s install, keybinds and `Game.log` checks (titled "Star Citizen"), `ask_game_root`, the focus check's wording, `GameLog` built by name for the one game-state part |
| `desktop/setup.py` | `Game.name = "Star Citizen"`, "Star Citizen LIVE wasn't found", the `Data.p4k` folder check |
| `settings.py` | `game_root` and the web search descriptions name Star Citizen; `Channel` comes from `game/install.py`; `Game` is a hand-kept list of pack ids |
| `input/windows.py` | `GAME_EXECUTABLE = "StarCitizen.exe"`, the fallback when a pack has no `window` |

### By default (13 files)

"When no pack is given, Star Citizen": `safety.py` (the gate's lists), `runtime/planning.py`, `runtime/session.py`, `commands/matcher.py` (three places), `lexicon/corrector.py`, `brain/briefing.py` (three), `brain/claude.py`, `brain/openrouter.py`, `brain_choice.py`, `desktop/commands.py`, `input/presser.py` (`game="Star Citizen"`), `packs/pack.py`, and `paths.py` (the player's file locations, which ADR 18 keeps where they are on purpose). `tile/design.py` and `knowledge/web.py` also read the Star Citizen pack when imported.

### By design (1 file)

`packs/parts.py`: the registry's ids for Star Citizen's built-in parts. That's what ADR 18 asks for.

### In comments or the product's description (7 files)

`__init__.py` (and the `tycho --help` line in `app.py`: "a voice co-pilot for Star Citizen"), `input/keys.py` (CryEngine's key names, which stay core: ADR 18), `stt/parakeet.py` and `stt/device.py` (the graphics memory the game takes), `runtime/messages.py`, `address.py`, `packs/__init__.py`. Nothing to move; the product's description is Ben's wording to change (#363).

### Imports of Star Citizen-only code

| Module outside `game/` and `knowledge/` | Imports |
|---|---|
| `app.py` | `game.install`, `game.store`, `game.gamelog`, `knowledge.fleet` |
| `brain_choice.py` | `knowledge` (ships, fleet, items, mining, trade, web) |
| `desktop/setup.py` | `game.install`, `game.store` |
| `desktop/bridge.py` | `game.store` |
| `settings.py` | `game.install.Channel` |

The other ten importers of `game/` only want `game/keybinds.py`'s general types.

## Part 4: Windows DLLs

`ctypes.windll` or `WinDLL` is used in `win32.py`, `runtime/instance.py`, `runtime/console.py`, `game/install.py`, `input/windows.py`, `ptt/windows.py` and `app.py` (a message box). #298 moves them into `win32.py`.
