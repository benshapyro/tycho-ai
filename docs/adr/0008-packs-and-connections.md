# 8. Packs and connections

Date: 2026-10-30 · Status: proposed. Pending: the author's sign-off. The player layer is accepted as memory (amendment below, 23 November 2026). The game-pack layer is settled by [ADR 18](0018-game-packs.md) (accepted 12 November 2026): data folders that name built-in parts by id.

## Context

Tycho starts with Star Citizen but may grow to other games, each with its own world, vocabulary, controls, data and atmosphere. Players will also want to shape Tycho themselves: its voice and sound, what it knows about them and their org, and which outside services it can reach, such as their org's Discord. ADR 4 already makes personas and data sources swappable. This decides how all of that fits together.

## Decision

**What Tycho knows and how it sounds is assembled from layers, in this order:**

1. **Core (fixed).** Tycho's behaviour and the safety rules: confirmations, the never-press list, the fact that every action starts from the player. Nothing below can change or remove these. A pack that says "eject without asking" is ignored and reported.
2. **Game pack.** Everything specific to one game:
   - a briefing on its setting and lore;
   - game terms for the speech corrector;
   - how to find and read keybinds;
   - action categories, and which actions count as dangerous;
   - fast-path commands;
   - data sources (UEX and the Star Citizen Wiki for Star Citizen).
   Star Citizen is the first pack.
3. **Persona pack.** Voice, sound treatment (a filter chain with presets), speaking style, form of address and sound cues. The default persona suits its game; players can swap or customise it.
4. **Player layer.** The player's own notes ("my org is X, I mostly fly a Constellation"), parts of packs switched on or off, and edits to the briefing.
5. **Live state.** What Tycho currently knows about the game: ship, location, recent events.

**Connections to outside services use MCP** (Model Context Protocol), the open standard for connecting assistants to tools, so the community can add connections without changing Tycho's code. Each connection is off until the player turns it on, and has its own permissions: read only, or allowed to act. Anything that speaks for the player, such as posting in Discord, always asks first.

**Packs are data, not code.** A pack is a folder of text and settings files that Tycho validates on load. Running code from a pack is out of scope; if it's ever needed, it goes through a connection instead.

## Consequences

- The Star Citizen-specific parts of the code (keybind reading, the lexicon, fast-path commands) move behind a game-pack interface over time.
- The prompt is assembled from layers, which keeps the core cacheable and stable, and makes it testable which layer said what.
- Audio settings (volume, filter preset and strength) and persona choices join the settings schema (ADR 5).
- Community packs and connections raise trust questions (what a pack can make Tycho say, what a connection can see), to be settled before any sharing feature.

## Amendment, 23 November 2026: the player layer becomes memory

*Status: accepted. The maintainer approved this text on 23 November 2026 (#569), after agreeing to the direction in the 0.11 plan ([#483](https://github.com/benshapyro/tycho-ai/issues/483)). Only the player layer is decided here; the rest of this ADR stays proposed.*

**Context.** Each request starts almost fresh: follow-ups last 30 seconds (ADR 14). The player's fleet (`fleet.toml`, ADR 15) and what to call them (`call_me`) are early, narrow kinds of the player layer above. Players want Tycho to remember lasting facts about them, without repeating them: "I mostly trade out of Area18", "keep answers short".

**Decision.** The player layer is accepted as **memory**: short notes about the player, kept on their PC, sent to the brain with each request.

- **Two kinds of note.** About the player in every game (how they like answers, their time zone), and about the player in one game ("my org is X", "I'm a level 42 hunter"). A game's notes go only with that game's requests.
- **Who writes** *(replaced by the second amendment below)*. The player, by saying "remember that...". Tycho may offer ("Want me to remember you fly a Cutlass?"), and saves only after the player's yes within the follow-up window. The brain never saves a note on its own.
- **On by default** *(maintainer, 23 November 2026)*, because nothing is kept unless the player says so. A `memory` setting turns it off; by voice it only turns off (`lower_only`, ADR 5).
- **Seen and forgotten.** "Forget that" or "forget that I fly a Cutlass" removes a note by voice, and a Memory page in the app lists every note with a delete button. The notes are a plain file the player can read and edit, like `commands.toml`: `%APPDATA%\tycho\memory.toml` for every game, and one per pack for each game.
- **Small.** At most about 50 notes per game and 50 for every game, each a sentence. When full, Tycho says so and saves nothing more until one is forgotten. Notes go in their own block of the request, outside the pinned, cached briefing, so the briefing's cache and its tested hash don't change.
- **Never above the core.** A note can't change safety, as the order of layers above says. "Remember to eject without asking" is refused, and no note can be read as permission to press. Notes are the player's own words, treated as untrusted text like a pack's: cleaned and cut before they reach the brain.
- **Fleet and `call_me` stay as they are**, beside memory, and show on the same Memory page. Folding them in is not decided here.
- **Where it goes.** To the brain's provider with each request, like what the player said (ADR 14's privacy list). Nowhere else: notes count as "anything the player said" in ADR 4's never-sent list, so never in crash reports or usage counts.

**Not decided here.** Tycho writing notes from past conversations without asking (#51), sharing notes between PCs, and edits to the briefing by the player.

**Consequences.**
- A memory store with a fake, its tools for the brain (save after a yes, forget), and the Memory page. Tests cover the size limit, the refused safety note, and notes going only with their own game.
- Memory cases join the brain eval: remember, forget, an offer the player declines, and "remember to eject without asking". The bar stays zero wrong presses.
- The README's "What leaves your PC" table gains a row: "Your memory notes, with each request, unless memory is off".

## Amendment, 23 November 2026 (second): Tycho notes game facts as you say them

*Status: accepted. The maintainer chose the direction and approved this text on 23 November 2026 (#589).*

**Context.** In the first version, Tycho saved a note only after "remember that..." or a yes to its offer. The maintainer found that too much friction to be useful: players state facts about how they play in passing ("I'm in my Cutlass", "heading home to Area18") and won't stop to say "remember".

**Decision.** "Who writes" becomes:

- **Game facts, saved as you say them.** When the player states a lasting fact about how they play this game (what they use, their home base, org, role, playstyle, the answers they like here), Tycho saves it to this game's notes without asking, and says so with the note in a few words ("Noted, you use a Cutlass."), so a mishearing is caught at once. "Forget that" right after removes it, and the Memory page shows it.
- **Only the player's own words.** Such a note holds only words the player said in this request, plus small words ("Uses a Cutlass" from "I'm in my Cutlass", never "Uses a Cutlass Black"). Nothing is saved unasked from look-ups, web pages, the game's log or Tycho's own replies, because one bad note would stay for a long time ("memory poisoning"). The memory tool checks this against what the player said, not what the brain says they said.
- **Everything else still asks first.** Notes for every game, and anything about the player outside the game (real name, where they live, health, work, other people), are saved only after "remember that..." or a yes to Tycho's offer, as before.
- **Unchanged:** never above the core (no note about pressing or confirming, checked when saved and again when read), at most about 50 per scope, plain files, the Memory page, the `memory` setting on by default and only off by voice, and where notes go.

**Still not decided.** Writing notes from past conversations (history, #484), and sharing notes between PCs.

**Consequences.** New eval cases: a stated game fact is saved and noted aloud, a personal fact isn't saved unasked, a fact from a look-up isn't saved, and "forget that" right after a save removes it. The bar stays zero wrong presses.

## Amendment, 23 November 2026 (third): the blueprints you own, from the game's log

*Status: proposed. The maintainer chose the direction on 23 November 2026 (#598). This text waits for the maintainer's approval.*

**Context.** In Star Citizen, a crafting blueprint is something the player owns for good once a mission gives it. Game.log records each one with the HUD's "Received Blueprint: <item>" line (#598). Players want to ask "do I have the Killshot blueprint?" or "which blueprints have I got?" in any session, not only the session where they got it. The second amendment rules out saving notes from the game's log. That rule is about notes, which are free text sent with every request. A list of owned blueprints is a different kind of thing: one item name per entry, checked against a fixed shape.

**Decision.** Tycho keeps a list of the blueprints the player owns, on their PC, beside the notes and the fleet.

- **Only from the game's own blueprint line, or the player.** An entry is added when Game.log's "Received Blueprint" line names an item that passes the item-name check (letters, digits and a few marks, at most 40 characters, the same check as the callout). An item the log names only by text key is added once the game's own text gives its name. The player can also add one ("I've got the Killshot Rifle blueprint"), because blueprints bought or earned before Tycho ran are otherwise missing. Nothing else in the log adds anything, and look-ups, web pages and Tycho's replies never do.
- **Earlier logs, read once.** On the first start after this ships, Tycho reads the game's saved logs (`logbackups`) once for that same line, so blueprints from before are listed. It keeps only the item names and dates, nothing else from those logs.
- **Used when asked, not sent with every request.** The blueprint look-up answers "do I have it?" and "which do I have?" from the list, and marks the blueprints it lists that the player already owns. The list isn't added to every request, so it doesn't grow the request or reach the brain unasked.
- **Seen and removed.** The list is a plain file, `packs\star-citizen\blueprints.toml`. The Memory page shows it with a delete button on each entry, and "forget the Killshot blueprint" removes one by voice. It holds each blueprint once, at most about 1,000.
- **Under the `memory` setting.** With memory off, nothing is added and the list isn't used. Where it goes is the same as notes: to the brain's provider only inside a look-up's answer, and never in crash reports or usage counts (ADR 4).
- **Never above the core.** An entry is an item's name, never an instruction. The check runs both when an entry is saved and when it is read.

**Not decided here.** Lists of other things the log records, and sharing the list between PCs.

**Consequences.** A blueprint store with a fake, the log reader feeding it, the one-time read of saved logs, and the look-up's "owned" answers. Tests cover the name check on save and on read, an item named by key, the size limit, memory turned off, and "forget". Brain eval cases cover "do I have the X blueprint?" and "which blueprints have I got?". The bar stays zero wrong presses. The README's "What leaves your PC" row for memory notes also names owned blueprints.
