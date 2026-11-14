# 19. Code layout: a thin entry point, game code behind its parts, and a size limit

Date: 2026-11-14 · Status: **proposed** (waiting on the maintainer). Answers the audit step of [#362](https://github.com/benshapyro/tycho-ai/issues/362), and plans what's left of [#61](https://github.com/benshapyro/tycho-ai/issues/61) and [#298](https://github.com/benshapyro/tycho-ai/issues/298). Builds on [ADR 18](0018-game-packs.md).

## Context

0.7 made Tycho work for more than one game. 0.8 gets it ready for players other than Ben, and for more packs and features after that. Each new feature now touches more files than it should.

The [code layout audit](../research/code-layout-audit.md) read the code on 14 November 2026 (0.7.0). What it found that shapes this decision:
- **`app.py` is 1,508 lines doing nine jobs**: the command line, seven commands, the start and restart loop, the desktop host, the session wiring, the voice and speech loaders, `tycho check`, Star Citizen's install questions, and push-to-talk helpers. The desktop imports five helpers back from it, so the two import each other.
- **Packs name their parts by id, but nothing builds a part from its id.** `app.py` and `brain_choice.py` build each Star Citizen part by name, behind `if` checks on the ids. A new part means editing both, and the wiring imports every Star Citizen module.
- **"No pack given" means Star Citizen in 13 files**, the safety gate among them. Its default lists include ones that loosen a check. Every running path passes its pack's lists today, so it isn't a live bug, but a second key-pressing game that forgot to would quietly get Star Citizen's.
- **The general keybind types live in Star Citizen's folder** (`game/keybinds.py`), so ten shared modules import from `game/` and it looks like a core dependency.
- **Nine files are over 500 lines.** `app.py`, `runtime/session.py` (847) and `desktop/bridge.py` (709) hold most of the tangle.
- Windows DLLs are loaded in seven modules (#298).

## Decision

### The target layout

Only what moves is shown. Everything else stays where it is.

```
src/tycho/
  cli/                 the command line, and nothing else
    __init__.py          main(), the parser, dispatch (the entry point: tycho.cli:main)
    commands.py          credits, actions, bind, sounds, key
    speech.py            model, gpu
    check.py             tycho check: prints doctor's findings, or JSON
  start/               turning settings into a running Tycho
    run.py               the start and restart loop (#295, #329), _start
    assemble.py          the pack's parts, the speech model, the voice, the session (build_session)
    serve.py             serve(), the keybind refresh
  doctor.py            every check as a Finding, printing nothing (#61): the CLI and the window share it
  keybinds.py          Action, KeyInput, ActivationMode, Keybinds, MODIFIERS: the general types, out of game/
  games/
    __init__.py          the one list of built-in parts: id -> builder
    star_citizen/        all of Star Citizen's code, today's game/ and knowledge/'s Star Citizen parts
      install.py, p4k.py, cryxml.py, localization.py, keybinds.py (the readers),
      store.py, layout.py, gamelog.py, uex.py, wiki.py, ships.py, ship_lookup.py,
      fleet.py, items.py, item_lookup.py, mining.py, refining.py, trade.py,
      trade_lookup.py, data.py (today's knowledge/store.py)
      checks.py          its tycho check findings: the install, keybinds, Game.log, asking for the folder
      parts.py           its builders, registered under "star-citizen", "ships", "fleet"...
  knowledge/           what's left is general: web search (its sites come from the pack), fresh.py's cache
  desktop/
    shell.py             the tray and window host, the starting star, first-run setup (out of app.py)
    bridge/              one js_api object, its methods grouped by page
  runtime/
    session.py           the Session, joining the stages
    speaking.py          starting, filling and stopping speech (out of session.py)
    asking.py            the brain request and the confirm question (out of session.py)
  win32.py             the only module that loads a Windows DLL (#298)
```

The data stays in `packs/star-citizen/` (ADR 18: a pack folder holds no code). The code goes in `games/star_citizen/`, so "Star Citizen's code" is one folder you can see, not "most of `game/` and part of `knowledge/`". A future game whose files need reading gets `games/<id>/` in a Tycho release, as ADR 18 says.

`app.py` goes away. `pyproject.toml`'s entry point becomes `tycho.cli:main`, and `packaging/tycho_main.py` and `tycho.spec` list the moved modules.

### Parts are built from their ids

`packs/parts.py`'s registry holds builders, not just ids. `games/__init__.py` lists every built-in part once, explicitly: no plugin discovery and no entry points, because a pack never runs code (ADR 18) and a part ships only in a Tycho release.

```python
# games/__init__.py
BUILT_IN = Registry(
    install={"star-citizen": star_citizen.parts.install},
    keybinds={"star-citizen": star_citizen.parts.keybinds},
    game_state={"star-citizen": star_citizen.parts.game_state},
    lookups={"ships": ..., "fleet": ..., "items": ..., "mining": ..., "trade": ..., "web": ...},
    checks={"star-citizen": star_citizen.checks.findings},
)
```

`start/assemble.py` asks the registry for the pack's parts in the pack's order (the brain's tool order is part of the prompt cache: keep it), and `doctor.py` asks it for the pack's checks. Neither names a Star Citizen module. A builder imports its own code when called, so start-up stays as fast as today's lazy imports.

### A pack is always given

Every function that takes a pack, or a pack's lists, takes it as a required argument: the safety gate, planning, the matcher, the lexicon, the briefing, both brain adapters, the presser's game name, the Commands page. Nothing loads a pack when it's imported. The tests get a `star_citizen` fixture and pass it.

**Why this is part of a refactor:** the gate's lists include ones only a built-in pack may set, because they loosen a check (ADR 18). A default that hands those to whoever forgets an argument is the kind of quiet path ADR 18 set out to close. Making it required turns a forgotten pack into a type error.

The player's file locations in `paths.py` stay as ADR 18 set them: Star Citizen's `commands.toml` and `fleet.toml` don't move. The setting names `game_root` and `game_channel` stay too, so nobody's `settings.toml` changes; their descriptions and `Channel` come from the pack's install part.

### A size limit

**No module in `src/tycho` is over 500 lines.** A test counts them.

- Files over it today are listed in the test with their current length as a ceiling. They can shrink, never grow, and a file leaves the list once it's under 500. The list only gets shorter.
- 0.8 takes `app.py`, `runtime/session.py` and `desktop/bridge.py` off the list (D3). The other six (`tile/draw.py`, `brain/openrouter.py`, `knowledge/items.py`, `commands/matcher.py`, `knowledge/ships.py`, `settings.py`) are split when a change next touches them seriously, not in a sweep.
- **Why 500:** a 500-line file can be read whole, by a person in one sitting and by an agent in one look, and today 102 of the 111 files already fit. A lower limit would split modules that have one job, such as the settings schema.

### Import rules, checked by a test

One test reads every module's imports (with `ast`, no running) and fails on:
1. **Star Citizen code outside its folder.** Only `games/star_citizen/` and the list in `games/__init__.py` import from `games.star_citizen`.
2. **Upward imports.** `cli` and `start` are the top: nothing imports them but `__main__` and `cli`. `desktop` doesn't import `cli` or `start` (today's `desktop → app` loop goes: its helpers move to `ptt/`, `audio/` and the Star Citizen install part).
3. **DLLs outside `win32.py`** (#298).

Like the size limit, the test starts with today's breaks listed and the list only shrinks. It's empty when D3 ends.

### The order of the moves (D3)

Small PRs, each green on Windows CI, each changing nothing a player can notice, each updating the AGENTS.md of what it touches. None needs a CHANGELOG line. A PR that touches the briefing or the brain's wiring also runs the Star Citizen keys-off eval (about $0.10) and shows the same score; the briefing's hash test stays pinned throughout.

| # | PR | Why here |
|---|---|---|
| 1 | The size and import tests, with today's breaks listed | Guard rails first: every later PR shrinks a list, and nothing can grow back |
| 2 | `keybinds.py`: the general types out of `game/` | Mechanical, and it removes ten of the imports into Star Citizen's folder |
| 3 | A pack is always given; no pack loaded on import | Small per file, and it closes the safety default |
| 4 | #298: every DLL through `win32.py` | Independent; D4 (hardening) waits on it |
| 5 | The registry builds parts; `assemble.py` and `doctor.py` ask it | The wiring stops naming Star Citizen |
| 6 | `games/star_citizen/`: move `game/` and Star Citizen's look-ups, with its checks and install questions | Now a move only, since nothing outside names the modules |
| 7 | Split `app.py` into `cli/`, `start/`, `doctor.py` and `desktop/shell.py`; the entry point changes | Last of the big ones, once its pieces have homes |
| 8 | Split `runtime/session.py` and `desktop/bridge.py` | Independent of the rest; can run beside 6 and 7 |

Tests patch `app` functions in 63 places; each PR moves those patches to the function's new home and renames the test file to match. No shims are left behind: an old import path that still works hides a missed move.

### What doesn't change

- Nothing a player sees or hears: the same commands, briefing (byte for byte), tools in the same order, settings, files and installer.
- The pack format and its rules (ADR 18).
- The boundaries and fakes (ADR 4), and the pure routing and planning (#55).
- `settings.py` still imports the lists its choices come from (models, voices, card kinds). The audit looked at it; copying those lists would be worse.

### Not chosen

- **Split `app.py` into sibling files and stop.** Cheaper (two PRs), but the wiring would still build Star Citizen by name and the safety default would stay. #362's "done" asks for both.
- **Put Star Citizen's code inside `packs/star-citizen/`.** One folder per game would be tidy, but ADR 18 says a pack folder holds no code, and that's what lets a pack be checked as data.
- **Find parts by entry points or a folder scan.** It would let an installed package add code to Tycho, which ADR 18 rules out.
- **A hard limit with no list.** It would force all nine files to split at once, in one risky PR, before anything else could merge.

## Consequences

- Star Citizen's code is one folder, and a test proves nothing else reaches into it. A second game with code adds `games/<id>/` and lines in one list.
- `tycho check`'s checks run without printing, so the window, onboarding (#364) and an agent (#196) can use them.
- The `app ↔ desktop` loop is gone, and the desktop host can be tested without the CLI.
- Every 0.8 item after D3 (wording #363, onboarding #364, the settings CLI #196, the Tile over WoW #381) lands in smaller files. That's why D3 comes before them.
- About eight PRs and two weeks of cloud work, with about $0.10 per eval check. Each is behaviour-neutral, so each can merge on green.
- Open branches that touch `app.py` will conflict with PR 7. The 0.8 small fixes (D1) should merge first; D3 checks the open PRs and says so on #362 before PR 7 starts.

### Follow-up issues

| # | Issue | Feeds |
|---|---|---|
| 1 | Size and import-rule tests, with today's breaks listed | D3 |
| 2 | Move the general keybind types to `tycho/keybinds.py` | D3 |
| 3 | Make the pack a required argument everywhere; no pack loaded on import | D3 |
| 4 | #298: one Win32 module (exists) | D3, D4 |
| 5 | Parts registry builds parts; `start/assemble.py`; `doctor.py` | D3, #364, #196 |
| 6 | `games/star_citizen/`: Star Citizen's code in one folder | D3 |
| 7 | Split `app.py` (closes the rest of #61) | D3 |
| 8 | Split `runtime/session.py` and `desktop/bridge.py` | D3 |
