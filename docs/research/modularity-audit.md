# Modularity audit: game packs and the Windows app

Audited 2026-11-11, on `main` at eb46443, for [#190](https://github.com/benshapyro/tycho-ai/issues/190) (game packs) and [#197](https://github.com/benshapyro/tycho-ai/issues/197) (a production-grade Windows app). The decisions it leads to are in [ADR 18](../adr/0018-game-packs.md) (accepted 12 November 2026).

This is a reference doc, not a spec. Everything here was read from the code. The field numbers #197 asks for (download sizes, start-up time, memory and CPU with the game running, antivirus) come from a run on the maintainer's PC and are added in the section left for them.

## Summary

- **About two thirds of the code knows nothing about the game.** Push-to-talk, the microphone, speech to text, the voice, the talking loop's threading, the Tile's drawing, the tray, the window and the settings are game-free, or nearly (a few user-facing strings name Star Citizen).
- **The game is wired in at eleven places, not behind one seam.** Most of it is already data (the commands, the lexicon, the game facts) or already behind a boundary (look-ups are `Lookup`s, game state is `GameState`). What isn't: the gate's confirm-grade list and mode guards, the briefing's text, the focus check's `StarCitizen.exe`, the game install finder, and `app.py`, which builds every Star Citizen part by name.
- **The hard part is safety, not loading.** The gate's knowledge of which actions are dangerous and which modes switch a key off is game knowledge. A pack has to supply it, and a pack must never be able to weaken it. ADR 18 answers that by letting a pack add danger, never remove it, and by trusting mode guards only from packs that ship inside Tycho.
- **Packs can be data only.** Every piece of Star Citizen code (reading `Data.p4k` and `actionmaps.xml`, `Game.log`, UEX and the Wiki) can stay built in, named by an id from the pack's data. An advisor-only game like World of Warcraft needs no game code at all for its first version.
- **The Windows app should be hardened, not rebuilt.** The shell is small, mostly sound, and its weak spots are fixable where they are: no record of native crashes, a tray or Tile that stops without the player knowing, Win32 declarations spread over seven files, no signing, no updater. A native shell around a local Python service would cost months, add a server to protect, and not unlock packs or the Mac any sooner.

## Part 1: what each module assumes about the game

"Game-free" means the module would work unchanged for another game. "Seam" is where ADR 18 puts the line between Tycho and a pack.

### Game-free

| Module | Notes |
|---|---|
| `ptt/` | Any key, mouse button or joystick button. `conflicts.py` checks the push-to-talk button against the game's keybinds, through the `Keybinds` type only. |
| `audio/` | Microphone and clips. |
| `stt/` | Parakeet, the model store, CUDA. A comment in `parakeet.py` sizes graphics memory for Star Citizen; the logic doesn't depend on it. |
| `voice/` | ElevenLabs, playback, the voice shortlist. "The Ship's Mind" is a voice name, a persona choice (ADR 8's persona layer), not game logic. |
| `runtime/` except `session.py` | Messages, ports, the action log, the console guard, the single-instance lock. `messages.py`'s `no_focus` comment names Star Citizen. |
| `input/plan.py`, `input/presser.py` | Press planning from activation modes, and the cancellable presser. They depend on the `Action`/`ActivationMode` types, which are general. The presser's refusal text says "Star Citizen isn't focused": a string, not logic. |
| `tile/board.py`, `tile/draw.py`, `tile/window.py` | What shows, the drawing, the window. |
| `desktop/` (tray, menu, host, window, keys) | The shell. `setup.py` and `bridge.py` have game parts, below. |
| `settings.py`, `spoken_settings.py`, `paths.py`, `credentials.py`, `logs.py`, `text.py`, `address.py`, `feedback.py`, `updates.py` | Settings descriptions mention Star Citizen and ships in places (the game folder, web search, the Tile's colour). `paths.py` has the fleet file and the RSI Launcher's log folder. |
| `brain/claude.py`, `brain/openrouter.py`, `brain/rounds.py`, `brain/decisions.py`, `brain/lookups.py` | The adapters and the tool loop. `rounds.py` checks that an answer credits its source ("UEX"); the source names come from each look-up's `credit`, so this is already per look-up. |

### Star Citizen, wired in

| Where | What it assumes | Already data or a boundary? | The seam (ADR 18) |
|---|---|---|---|
| `game/install.py` | The RSI install folder, channels (LIVE, PTU, EPTU, TECH-PREVIEW, HOTFIX), the launcher's log lines, `TYCHO_SC_ROOT` | No: code | A built-in **install finder**, named by the pack (`star-citizen`). |
| `game/p4k.py`, `game/cryxml.py`, `game/localization.py`, `game/keybinds.py`, `game/store.py`, `game/layout.py` | Default keybinds in `Data.p4k` as CryXmlB, the player's changes in `actionmaps.xml`, `kb1_`/`mo1_` prefixes, action maps, activation modes | The types (`Action`, `KeyInput`, `ActivationMode`, `Keybinds`) are general; the readers are Star Citizen's | A built-in **keybind source** returning `Keybinds`. The types stay core. `layout.py` (the recommended-keybinds profile) is a Star Citizen tool and stays with the source. |
| `game/gamelog.py` | `Game.log` lines: route plotted, arrived, left the seat. The fact `QUANTUM_ROUTE` | Behind `GameState` already | A built-in **game-state source**, named by the pack. The fact names it can report are listed in the pack's data, so the gate and the command loader know them. |
| `commands/commands.toml` | 534 lines of Star Citizen commands and sequences, by action name | Data | Moves into the pack folder as is. `matcher.py` is game-free. |
| `commands/reserved.py` | Confirm, cancel and "say again" words | Game-free words | Stays core. |
| `lexicon/terms.txt` | Star Citizen places, ships and words | Data | Moves into the pack. `corrector.py` is game-free. |
| `safety.py` | `CONFIRM_GRADE` (eject, self-destruct, missiles, respawn...), `TRAVEL` (quantum and jump), `_MODE_GUARDS` (NAV and quantum mode switch off weapons), `_STATE_GUARDS` and `_REQUIREMENTS` (`QUANTUM_ROUTE`), the danger-word backstop | Code, holding game knowledge | The **rules stay core**: the check order, the clash check, the backstop words, "a pack can add, never remove". The **lists** move to the pack's `safety.toml`. See ADR 18 for which of them a pack is trusted with. |
| `brain/briefing.py` | `INSTRUCTIONS` mixes core rules (how to reply, never pick dangerous actions, "Copy.") with Star Citizen (the setting, UEX, RSI's roadmap, quantum and the star map). `UNBRIEFED_ACTIONMAPS` and `CONTINUOUS` are Star Citizen action maps and action names | Code (a string), plus `facts.md` as data | Split the text into a **core part** and a **pack part**, joined so Star Citizen's briefing stays byte for byte the same (the prompt cache depends on it). The action filters move to the pack's data. |
| `brain/facts.md` | The current patch's facts | Data | Moves into the pack. |
| `knowledge/` | UEX, the Wiki, ships, items, mining, refining, trade, the fleet, web search on Star Citizen sites | Each look-up is a `Lookup` already | Built-in **look-ups**, each with an id. The pack lists the ids it uses. `web.py`'s `SITES` becomes pack data (the sites a search may read), since it's a list, not code. |
| `input/keys.py` | Star Citizen's key names ("ralt", "np_add") to Windows codes | Mostly general: these are CryEngine's names for keys | Stays core for now, as the key-name vocabulary of the `Keybinds` type. A second keybind source translates its game's names into these. |
| `input/windows.py` | `GAME_EXECUTABLE = "StarCitizen.exe"` | A constant, behind `FocusCheck` | The pack names its **window to watch** (the executable). |
| `tile/design.py`, `tile/display.py`, `tile/cards.py` | Manufacturer accents by ship prefix (`DRAK_`), the "ship" colour setting, the `ship` and `fleet` card kinds | Accents are a table (data); the card kinds are a schema | Accents move to the pack's `tile.toml`. The card kinds stay core: `ship` and `fleet` are general enough ("specs side by side", "the player's own things ranked for a job"), and a pack can leave kinds out of the briefing. |
| `runtime/session.py` | Two comments, and the "looking it up" filler line naming ships | Game-free logic | Nothing to move. #55 splits it for other reasons. |
| `desktop/setup.py`, `desktop/bridge.py` | "Star Citizen LIVE wasn't found", `Data.p4k` in the folder check, a sample card sourced "UEX" | Strings and one check | The setup page asks the pack's install finder, and shows the pack's name. |
| `app.py` | Builds every Star Citizen part by name: `GameInstall`, `KeybindStore`, `GameLog`, `ShipLookup`, `UexApi`, `WikiApi`, `Fleet`, `ship_accent`, the default commands and lexicon | Wiring | The wiring asks the loaded pack instead. This is the change with the most lines. |
| `tools/bench/tycho_eval.py`, `tools/spike/brain_cases.toml` | Star Citizen's eval cases | Data | The cases move into the pack, and the eval takes `--pack`. |
| `plugins/tycho/` skills | Setup and commands for Star Citizen | Docs | Updated when a second pack ships. |

### Counts

From a word search for Star Citizen terms (the game's name, UEX, p4k, action maps, quantum, ship, manufacturer prefixes, `v_` action names) over `src/tycho`, 2026-11-11:

- **Heavy** (more than 20 hits): `knowledge/ships.py`, `knowledge/fleet.py`, `knowledge/refining.py`, `knowledge/items.py`, `knowledge/store.py`, `app.py`, `knowledge/trade_lookup.py`, `knowledge/ship_lookup.py`, `game/install.py`, `tile/design.py`, `brain/briefing.py`, `safety.py`.
- **Light** (1 to 3 hits, mostly strings and comments): `session.py`, `input/*`, `brain/claude.py`, `brain/rounds.py`, `lexicon/corrector.py`, `commands/matcher.py`, `voice/voices.py`, `stt/*`.
- **None**: `ptt/`, `audio/`, `runtime/` (but session and messages), `desktop/` (but setup and bridge), `tile/board.py`, `tile/window.py`.

By lines: `game/` and `knowledge/` are about 4,700 of the 19,300 lines in `src/tycho`. With `commands.toml`, `terms.txt`, `facts.md`, the game parts of `safety.py`, `briefing.py` and `design.py`, and `app.py`'s wiring, the Star Citizen share is about a third. #190's first count of "about 60% game-independent" holds, and is a little low.

## Part 2: what #197 asked, answered from the code

### What players notice

| | Today | From |
|---|---|---|
| Install | Inno Setup, for the current user only, no administrator prompt, into `%LOCALAPPDATA%\Programs\Tycho`; adds `tycho` to PATH | `packaging/tycho.iss` (`PrivilegesRequired=lowest`) |
| SmartScreen | Warns: the installer and programs aren't signed (#9) | README |
| Downloads | The installer, then the speech model (2.5 GB) on first run; CUDA libraries (1.2 GB) only with `tycho gpu install` | README |
| Uninstall | Keeps settings, keys and the speech model | README |
| Updates | Tycho checks GitHub's releases API once at start and says a newer version is out. It never updates itself | `updates.py` |
| Start-up, memory, CPU | *Waiting for the field numbers* | P1 run, below |

### What breaks in the field

- **Antivirus.** PyInstaller programs that aren't signed are often flagged. Nothing in the build reduces this today beyond `upx=False` (packing makes it worse). Signing (#9) is the main fix; submitting false positives to Microsoft is the other.
- **Missing runtimes.**
  - WebView2: ships with Windows 11. On Windows 10 without it, the window can't open; Tycho says so and keeps working without the window (ADR 17).
  - Visual C++ runtime: PyInstaller bundles the DLLs Python needs. ONNX Runtime's are covered by the same bundle; worth a check on a clean Windows 10 machine.
  - CUDA: optional and downloaded by Tycho (`stt/cuda.py`), never required.
- **Non-admin installs** work by design (per-user install).
- **Several Windows users on one PC.** Each gets their own install, settings, keys (Credential Manager is per user) and a copy of the 2.5 GB speech model. The single-instance lock is a named mutex; worth a check that it's per session (`Local\`), so two signed-in users can each run Tycho.
- **High DPI and several monitors (the Tile).** The Tile reads the screen size it draws on; multi-monitor placement and per-monitor DPI are untested in game (ADR 16's open in-game checks, #2).

### What's fragile in the code

- **Win32 through ctypes, loaded in seven files.** `win32.py` declares argument and return types for the functions the Tile and the tray share (36 `argtypes`). `input/windows.py`, `ptt/windows.py`, `runtime/instance.py`, `runtime/console.py`, `game/install.py` and `app.py` (the error message box) load the DLLs themselves, with their own declarations, some partial. The clearest case is the focus check (`input/windows.py`): `GetForegroundWindow`, `OpenProcess` and `CloseHandle` have no declared types, so their handles come back as 32-bit ints. Windows keeps those handle values small today, so it works, but it's the kind of thing that breaks quietly on a 64-bit build. Fix: every Win32 call goes through `win32.py`, fully declared, and a test checks no other module loads a Windows DLL.
- **Threads.** There are six: the main thread (the window), the voice loop (asyncio), the Tile's window, the tray, push-to-talk polling, and short-lived ones (the update check, the "open the window" listener, the GPU switch). The hand-offs are sound: the voice loop is reached only through `call_soon_threadsafe` (`desktop/host.py`), the Tile and tray through posted messages, and the window through the bridge, which waits at most 2 s for a change to apply.
- **When one part crashes.**
  - The Tile and the tray catch any exception on their thread, log it and stop, so Tycho keeps listening. But the player isn't told: with no tray icon there's no Quit and no way back to the window except starting Tycho again from the Start menu (the single-instance lock then opens the window).
  - A Python exception that ends the voice loop is passed to the main thread and shown in a message box by `tycho-app.exe` (`packaging/tycho_main.py`).
  - **A native crash leaves nothing.** An access violation in a ctypes call, ONNX Runtime, PortAudio or .NET (under pywebview) ends the process with no message and no log line. Nothing turns on `faulthandler`. Fix: write native crash tracebacks to a file in the logs folder, and say on the next start that Tycho stopped unexpectedly, with that file.
- **Keys held at a crash.** Every press releases in a `finally` (`input/presser.py`), which a native crash skips. Windows releases synthetic key-downs when the sending process dies only in some cases. Worth a test on the PC: kill Tycho mid-hold and see whether the game sees the key stuck.

### The options

| | Harden the current stack (recommended) | Native shell, Python core as a local service | Rewrite the core |
|---|---|---|---|
| What | Sign (#9), record native crashes, tell the player when the Tile or tray stops, put all Win32 in one fully declared module, an updater that downloads the next installer and checks its SHA-256, a winget manifest | Window, tray and Tile in a native framework (WinUI 3 or C#, or Tauri); the Python core runs as a second process serving 127.0.0.1 with a token | Not considered: out of scope for #197 |
| Rough cost | About 2 to 3 weeks of PR work, plus the signing account (#9) | 2 to 3 months: a second language and toolchain, a protocol between the two, a server to secure and test, two processes to start, stop and update together | |
| Risk | Low: each fix is a small PR with tests | High: everything the shell does today must be redone, and the Tile's drawing (ADR 16) moves or is duplicated | |
| What it unlocks | Signed, self-updating, diagnosable installs: what players notice most | A Mac shell could share the core, but the Mac still needs its own shell, key sender and Tile (#198) | |
| What it doesn't | It keeps Python in the shell: the window stays about 285 MB while open (ADR 17), freed when closed | It doesn't shrink the download (the core is still frozen Python with ONNX Runtime) or make packs easier | |

**Why not the native shell now.** Packs (#190) live entirely in the core, so a new shell doesn't help them. The Mac (#198) needs per-platform code for the parts that are already behind boundaries (key sender, focus check, push-to-talk, the Tile window, the tray); pywebview already runs on the Mac. A local server is a new attack surface that ADR 17 avoided on purpose. Revisit if the window's memory or start-up time becomes a real complaint, or if a Mac version is approved.

### The field numbers (P1)

*Left for the PC run: installer size, speech-model download size, time from start to "ready", memory and CPU at idle, and with the game at its menu if the maintainer allows. Anything antivirus or SmartScreen flags.*

## Part 3: the follow-up issues

ADR 18 lists the issues this audit leads to, and which brief each feeds. They are filed now that the maintainer has approved it.
