# Look-ups (ADR 15)

What the brain can look up: ships, items and crafting, mining, trade prices and routes, refining, ships for aUEC, the player's fleet, and web search. Read the root AGENTS.md and `src/tycho/brain/AGENTS.md` first.

## Sources and their terms

- **UEX** (`uex.py`, `trade*.py`, `refining.py`): an official API, 120 requests a minute, for personal and non-commercial use. Credit UEX in what's said: `credit = "UEX"` makes the brain add "From UEX." if Claude didn't.
- **Star Citizen Wiki API** (`wiki.py`, `items*.py`, `mining.py`, ships): data from the game files. It requires credit and allows no commercial use.
- **Never** scrape a site whose terms forbid it. Erkul blocks AI crawlers, and RSI's terms forbid bots (the maintainer decided against its status feed, #154).

## Rules

- **Resolve names in code, not by Claude retrying.** Speech-to-text writes "quantanium", "S F nine", "cutty black". Match against the source's own names, try a few spellings, and keep a fuzzy cutoff high enough that "unobtainium" isn't Quantainium (0.88 in `mining.py`).
- **A best guess beats a question back.** With two to four matches, answer the likeliest and name the others (`also_matches`, `also_places`). Above `GUESS_AMONG`, say "name one".
- **A miss is a result** (`not_found`, closest names), not `failure()`. Keep `failure()` for the source being down.
- **One request beats many.** A commodity's routes come in one `/commodities_routes?id_commodity=N` call, not station by station. UEX caps that list at 500 (`ROUTES_CAP`).
- **Results are compact JSON with their source.** Each is a tool result in Claude's context, so keep it small.
- **Time:** a look-up has `seconds` (4-6 s), and the request has a deadline (`LOOKING_SECONDS`, `LOOKING_LIMIT` in `runtime/session.py`).

## Testing

- `tests/fakes/fake_uex.py` and `fake_wiki.py` script the APIs, so tests need no network.
- Check a change against the live API too (free): run the look-up directly with `uv run python -c ...`, using the questions from `tools/spike/brain_cases.toml`.
