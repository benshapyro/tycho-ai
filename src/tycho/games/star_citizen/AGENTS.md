# Star Citizen's code (ADR 19)

Everything Tycho does that only Star Citizen needs: its install and channels, `Data.p4k` and CryXmlB, its keybinds and the keybind store, `Game.log`, its part of `tycho check` (`checks.py`), and its look-ups. Read the root AGENTS.md first.

- **Look-ups** (`uex.py`, `wiki.py`, `ships.py`, `ship_lookup.py`, `fleet.py`, `items.py`, `item_lookup.py`, `mining.py`, `refining.py`, `trade*.py`, `data.py`): follow `src/tycho/knowledge/AGENTS.md`, which has the rules for every look-up and each source's terms.
- **Only `tycho.games` imports this package** (`tests/test_code_layout.py`). The rest of Tycho calls `tycho.games`' functions and types their results with `packs.parts`' protocols. A new part goes in `tycho.games.BUILT_IN` with a builder that imports its module when called.
- **This package's `__init__` imports nothing**, so loading one module never loads the rest (#411). Import modules: `from tycho.games.star_citizen.wiki import WikiApi`.
- The data (commands, words, safety lists, briefing) is the pack in `src/tycho/packs/star-citizen/`, not here.
