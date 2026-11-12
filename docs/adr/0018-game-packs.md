# 18. Game packs: data folders, built-in readers, and a hardened Windows app

Date: 2026-11-11 · Status: accepted (the maintainer, 12 November 2026). Settles the game-pack part of [ADR 8](0008-packs-and-connections.md), and answers [#190](https://github.com/benshapyro/tycho-ai/issues/190) and [#197](https://github.com/benshapyro/tycho-ai/issues/197).

## Context

Tycho should work for more than one game. One install should serve several games, a new game should reuse everything that works, and Star Citizen players should notice nothing when Star Citizen becomes the first pack. Some games forbid tools that press keys, so a pack must be able to turn key actions off ([#191](https://github.com/benshapyro/tycho-ai/issues/191)). The next game is likely World of Warcraft, as an advisor only (the 0.7 plan, agreed 11 November 2026).

[ADR 8](0008-packs-and-connections.md) (proposed) layers what Tycho knows: core, game pack, persona, player, live state. It says packs are data, not code. #190 asked whether a pack should be a `GamePack` Protocol with code, or a folder of data plus code.

The [modularity audit](../research/modularity-audit.md) read the code on 11 November 2026. What it found that shapes this decision:
- Star Citizen is wired in at eleven places. Most are already data (commands, the lexicon, the game facts) or already behind a boundary (look-ups, game state, the focus check).
- The parts that are code (reading `Data.p4k` and `actionmaps.xml`, `Game.log`, UEX and the Wiki, finding the install) are specific to Star Citizen's files and services. No second game would reuse them.
- The gate's lists (which actions are dangerous, which modes switch a key off) are game knowledge, and some of them loosen a check rather than tighten it.
- The Windows shell's weak spots are fixable where they are.

## Decision

### A pack is a folder of data

A game pack is a folder that Tycho validates on load. It holds no code, and Tycho never runs anything from it (ADR 8 stands). Game code that a pack needs, such as a keybind reader, is **built into Tycho and named by an id** in the pack's data. A new game that needs new reading code needs a Tycho release; a new game that can use what's built in needs only a folder.

```
packs/star-citizen/
  pack.toml        the manifest: id, name, version, format, mode, the built-in parts it uses
  briefing.md      the game part of the brain's briefing
  facts.md         what's true in the current patch
  terms.txt        game words for the speech corrector
  commands.toml    fast-path commands and sequences (only in a pack with key actions)
  safety.toml      confirm-grade actions, travel actions, mode and state guards
  tile.toml        accents (by maker), and the card kinds the briefing offers
  eval/cases.toml  the pack's own brain eval cases
```

The manifest:

```toml
id = "star-citizen"
name = "Star Citizen"
version = "1.0.0"          # the pack's own version, shown by tycho check and in traces
format = 1                 # the folder format; Tycho refuses a format it doesn't know
mode = "keys"              # or "advisor": see below
window = "StarCitizen.exe" # the program whose window must be in front to press

[parts]                     # built-in code, by id
install = "star-citizen"    # find the game folder and its channels
keybinds = "star-citizen"   # Data.p4k defaults plus actionmaps.xml
game_state = "star-citizen" # Game.log
lookups = ["ships", "fleet", "items", "mining", "trade", "settings", "web"]

[web]
sites = ["robertsspaceindustries.com", "starcitizen.tools", "uexcorp.space",
         "erkul.games", "sc-craft.tools", "scmdb.net"]  # the only sites web search may read
```

In code, the loader turns the folder into one frozen `GamePack` value. The built-in parts sit in a registry by id, each behind a Protocol with a fake (ADR 4): `InstallFinder`, `KeybindSource` (returns the existing `Keybinds`), `GameState` (exists), and `Lookup` (exists). The `settings` look-up is core and every pack gets it. A pack that names an id Tycho doesn't have fails to load, with a plain message.

**Why not a Protocol that packs implement in code.** It would make a pack a program: it could press keys, read files and reach the network, and every pack would need a code review before anyone could use it. The audit found nothing a pack needs that a named built-in part and data can't give. If a game ever needs code no release ships, it goes through a connection (ADR 8), never into a pack.

### Which modes a pack allows

`mode` is set by the pack, from the game's rules:

- **`keys`**: Tycho may press keys, through the safety gate, as today.
- **`advisor`**: Tycho never presses anything (#191). It answers, looks things up and shows cards.

In advisor mode:
- **The gate refuses every press, first, whatever asks.** It's the first check in `safety.check`, so no command, sequence, brain output or confirmation can get past it.
- The brain is offered no `run_actions` or `run_sequence`, and the briefing says Tycho can't press keys in this game, and why.
- The pack may not have `commands.toml`, a `keybinds` part or `window`: the loader refuses an advisor pack that has them.
- The app and `tycho check` say which mode the game is in, and why, in the pack's words.

The player can't turn key actions on in an advisor pack. In a `keys` pack the player may choose advisor mode for themselves (a setting that, like ADR 5's `lower_only`, can only make Tycho do less).

### Safety: a pack can add danger, never remove it

ADR 8's layer 1 holds. In `safety.py`, **the rules stay core**: the order of checks, the key-clash check, the confirm question, and the danger-word backstop (eject, self-destruct, jettison, respawn, missiles, attack, fire). A pack supplies **lists**:

| In `safety.toml` | What it does | Who may set it |
|---|---|---|
| `confirm` | Actions that always ask first | Any pack. It only adds: the backstop words still apply on top |
| `travel` | Actions that ask only when the brain chose them from looser words (ADR 14) | Any pack. It only adds asking |
| `not_danger` | Harmless actions the backstop words would catch ("fire_mode") | **Built-in packs only** |
| `mode_guards`, `state_guards`, `accepted_risks` | Setup actions and game facts that switch a control context off, so a key it shares with a dangerous action is safe to press | **Built-in packs only** |
| `facts` | The facts the pack's game-state part can report, with the sentence the gate says when one is missing | Any pack |

The lists that loosen a check (the last two "built-in only" rows) are trusted only from packs that ship inside Tycho, reviewed in a pull request like any code. Until there's a feature for sharing packs (ADR 8's open trust questions), every pack ships inside Tycho anyway; the rule is written now so sharing can't quietly change it. A pack that says "eject without asking", by leaving eject out of `confirm` or putting it in `not_danger` from outside, is refused and reported, and a test proves it.

### The briefing

`brain/briefing.py`'s text splits into a **core part** (how to reply, the safety rules, "Copy.", cards) and the pack's `briefing.md` and `facts.md`. The action filters (`UNBRIEFED_ACTIONMAPS`, `CONTINUOUS`) move to the pack's data. They are joined so that **Star Citizen's briefing stays byte for byte what it is today**: a test pins its hash, because a changed briefing rewrites the prompt cache and changes eval scores.

### Switching packs

- A setting, `game`, holds the pack's id. Default `star-citizen`, so an existing `settings.toml` needs no change. It changes in the app or the file, not by voice, and applies at restart, since keybinds, the briefing and the look-ups are all loaded at start.
- The app's Settings lists the installed packs. Picking one restarts the voice loop.
- The player's own files are per pack: `%APPDATA%\tycho\packs\<id>\commands.toml`. Star Citizen keeps reading `%APPDATA%\tycho\commands.toml` (and `fleet.toml`) where they are, so nobody's phrases move.
- Choosing the pack from the game in front (by `window`) is left for later; it needs care, because a press must never go to the wrong game.

### Versions and the pack's own eval

- `version` is the pack's own (semantic versioning). `format` is the folder format; a Tycho release reads its own format and older ones it lists.
- The version and a hash of the assembled briefing go in the log at start and in each brain trace (#282), so a score or a bug report names the pack it came from.
- Each pack has `eval/cases.toml`, in the format of today's `tools/spike/brain_cases.toml`. `tools/bench/tycho_eval.py` takes `--pack` and runs that pack's cases. Star Citizen's cases move there. A pack with key actions must pass the bar Star Citizen does: zero wrong presses ([#112](https://github.com/benshapyro/tycho-ai/issues/112)). An advisor pack's cases include press requests that must be refused.
- The pack loader and every built-in part are tested with the existing fakes. A second, **fake** pack in `tests/` with its own keybinds and commands proves the seam presses keys for a game that isn't Star Citizen.

### Star Citizen becomes the first pack, with no change for players

Its data moves to `packs/star-citizen/` inside the package. Its code stays where it is (`game/`, `knowledge/`), registered under ids. The same commands, phrases, confirm-grade list, look-ups, Tile colours and briefing (byte for byte). The existing tests pass unchanged.

### The Windows app: harden it, don't rebuild it (#197)

Keep Python, PyInstaller, pywebview, the Win32 Tile and the tray. Fix the weak spots the audit found:
1. **Sign** the installer and programs (#9).
2. **Record native crashes.** Turn on `faulthandler` to a file in the logs folder; on the next start, say Tycho stopped unexpectedly and where the file is. Reporting it anywhere stays opt-in (#199).
3. **Say when a part stops.** If the Tile or the tray thread stops, tell the player once (a notice card or a spoken line) and keep listening.
4. **One Win32 module.** Every Windows DLL call goes through `win32.py` with full argument and return types; a test checks no other module loads one.
5. **An updater the player starts.** "A newer version is out" offers to download the installer, checks its SHA-256 against the release, and runs it. Never in the background, never while the game is focused.
6. **winget**, once the installer is signed.

A native shell (WinUI, C# or Tauri) around the Python core as a local service is not recommended now. It costs about 2 to 3 months against about 2 to 3 weeks, adds a local server that ADR 17 chose not to have, and doesn't help packs (which live in the core) or the Mac (which needs its own shell parts either way). Revisit if the window's memory or start-up becomes a real complaint, or a Mac version is approved (#198).

### How this changes ADR 8

ADR 8's game-pack layer is settled here: data only, with built-in parts named by id. "How to find and read keybinds" and "data sources", which ADR 8 lists as pack contents, are the pack naming a built-in part, not the pack holding code. "Which actions count as dangerous" is a pack list that can only add. ADR 8's status line notes that its pack layer is settled by this ADR. Personas, the player layer and connections are unchanged and still proposed.

## Consequences

- **More data files, less code per game.** Star Citizen's game knowledge moves out of four Python modules into its folder. `app.py` asks the pack instead of building Star Citizen's parts by name, which is most of the change.
- **A new game with new files needs a Tycho release**, not just a pack. That's deliberate: game-reading code gets reviewed like the rest of Tycho.
- **Packs ship inside Tycho for now.** A folder the player adds isn't loaded until the trust questions in ADR 8 are answered.
- **Builds on #55.** Routing and planning become pure functions first (brief C3), so the pack's lists reach one place, and the eval grades the same code.
- **Starts the Mac groundwork without promising it.** The pack's `window`, the key sender and the focus check are already boundaries; the Mac needs its own versions (#198).

### Follow-up issues

| # | Issue | Feeds |
|---|---|---|
| 1 | Pack loader: `pack.toml`, the format, validation, the registry of built-in parts, and the fake test pack | C5 |
| 2 | Move Star Citizen's data into `packs/star-citizen/`, with the briefing hash pinned | C5 |
| 3 | The gate's lists from the pack: `safety.toml`, built-in-only loosening lists, and the "eject without asking" refusal test | C5 |
| 4 | `app.py` wires the loaded pack; the `game` setting; per-pack player files | C5 |
| 5 | The pack's eval cases and `tycho_eval.py --pack` | C5 |
| 6 | Advisor mode: the gate's first check, no act tools, the briefing line, the app and `tycho check` say so (#191) | C6 |
| 7 | Switching packs in the app's Settings | C6 |
| 8 | The World of Warcraft pack, advisor only, after the data-licence check | C7 |
| 9 | Record native crashes, and say so on the next start | #197 |
| 10 | Tell the player when the Tile or the tray stops | #197 |
| 11 | One fully declared Win32 module | #197 |
| 12 | An updater the player starts, with a SHA-256 check; winget after signing | #197 (after #9) |
