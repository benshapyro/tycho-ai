---
status: draft
parent: docs/work/1.1-game-data/README.md
---

# Plan: game data from the player's own install, place ids first (#690)

## The idea

Route plotting gets place names from UEX's website today, and checks the game's route line by its words: `levski_all-001` counts as Levski because it contains "levski" (ADR 3, 25 Nov amendment). That amendment says it lasts "until Tycho reads the game's own list".

The game's own database sits inside the player's `Data.p4k`. Tycho already reads single files from that archive for keybinds (`games/star_citizen/p4k.py`). This plan reads the list of places from it, once per patch, on the player's own PC.

Example: Game.log says the route went to `rs_ext_pyro3_leo`. Today Tycho can't name that, so it reports "not checked". With this, Tycho knows that id's shown name and says the route is done.

## What we know

- Community tools unpack the player's own `Data.p4k` after each patch into a per-patch cache and need no network after that. They redo it when the archive's size changes, and handle the launcher locking the file during an update (**stated**, audit 26 Nov).
- Tycho can already open the archive, find a file and decompress it, and can read the game's text strings (`localization.py`, which turns `@` keys into shown names).
- Where the place records sit inside the game's database, and their exact layout, is **unknown** until the spike.
- Game data can't go in the repo (no licence to share it), so it's built on each PC, like the ship data copy (`data.py`, ADR 15).

## The plan, step by step

**Step 1: spike (cloud can't do it; one PC session, no player-facing change).** On Ben's current patch, read the database file out of `Data.p4k` and write a small throwaway script that lists each place's id beside its shown name. Done when `levski_all-001` maps to Levski and `rs_ext_pyro3_leo` to its name. Note the time and memory it takes, and the file layout found. The script stays under `tools/spike/`, never in the app. Output: a short results note in this folder.

**Step 2: the reader (our own code).** In `games/star_citizen/`, a small reader for the database format, written from public format descriptions, that pulls only the place records: id, shown-name key, system, and kind (city, station, outpost, moon, planet). It refuses anything it doesn't understand with a clear error instead of guessing. No bundled third-party unpacker.

**Step 3: the per-patch copy.** Like the ship data: one read-only SQLite file per patch in Tycho's data folder, rebuilt when `Data.p4k`'s size or date changes. If the launcher has the archive locked (mid-update), Tycho says "try later" and uses the last copy, never a crash. Built in the background at start, so the first route request doesn't wait.

**Step 4: a new places part.** A second implementation of the existing `Places` interface (`packs/routes.py`: `find`, `guessed`, `name_of`), backed by the local copy. The pack's `places = "star-citizen"` points at it. `name_of` finally answers for every id, so the word check can go. UEX's list is used only while the first copy is still being built.

**Step 5: ADR 3 amendment** (Ben's call, drafted in the PR for step 4): route names and id checks come from the player's own patch data; UEX's list and the word check go.

**Later, only if the Wiki falls short:** missions and blueprints offline from the same database. Not part of 1.1.

## Tests and eval

- A small made-up database sample in `tests/` (never real game data) that the reader parses, plus a broken one it refuses.
- Rebuild tests: size changed means rebuild; locked archive means old copy plus a "try later" note.
- Route tests: a plotted id is matched to the chosen place by the local list; an id the list lacks is still "not checked", never "done".
- At least one test fails on today's word check (an id with no shared word, like the Pyro one).
- Upgrade test: a player with no local copy yet still plots through UEX.

## Decisions for Ben

1. **Replace UEX for routes.** *Recommendation:* yes, once the spike maps every place on one patch. Keep UEX only as the stand-in while the first local copy builds. Needs an ADR 3 amendment, which you approve.
2. **Reading the game's database file is within ADR 3.** *Recommendation:* yes. It's a file on disk that Tycho already opens for keybinds: no memory reading, no injection. Worth a line in the amendment so it's on record.
3. **Places only in 1.1.** *Recommendation:* yes. Missions and blueprints stay on the Wiki API, which works.

## Size

Step 1: one PC session. Steps 2 to 4: two PRs, cloud. Step 5 rides with step 4.
