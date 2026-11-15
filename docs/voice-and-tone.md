# Tycho's voice and tone

How Tycho words what it says and shows (#363). Read it before you write or change a line a player hears or reads: a spoken reply, a line of the brain's briefing, an error, a setting's description, a label in the window, a line of `tycho check`.

The first half is the guide. The second half lists where every such line lives, and what's still to do.

## The guide

Tycho talks to someone who is busy playing. They hear a line once, at speed, and can't scroll back.

1. **Plain words.** Write as you'd say it to a friend at the next desk. "Couldn't reach ElevenLabs", not "TTS request failed". No internal names (a module, an exception, a status code alone) in a line a player sees: say what happened in their terms, and put the detail in brackets after it if it helps a bug report.
2. **Short spoken lines.**
   - The line said with a key press is a few words: "Gear down, lights on." It names what was sent, never a result Tycho can't see.
   - A simple question gets one short sentence. A yes-or-no question gets the yes or no and one reason.
   - Only a question that asks for an explanation gets more: three or four plain sentences at most, then an offer of the rest.
   - More than about three items or numbers goes on the Tile, with one spoken sentence.
   - No preamble, no repeating the question, no "As an AI".
3. **No blame.** Say what happened, not what the player did wrong. "That key looks cut short. Copy it again, all of it.", not "Invalid key".
4. **Say what to do next.** A line that reports a problem ends with the way out: a setting, a command to run, or what to say. If there's nothing the player can do, say that it's fine or that Tycho carries on ("so Tycho speaks as George").
5. **Say why.** "Tycho couldn't answer" is never the whole line. Give the cause in a few words: "can't reach Claude (offline?)", "the question expired".
6. **A game's words come from its pack.** The core (everything outside `src/tycho/games/<game>/` and `src/tycho/packs/<game>/`) names no game and uses no game's terms: no "Star Citizen", "quantum", "ship" or "Commander" there. A core line that needs the game's name takes it from the pack (`pack.name`). Examples in the brain's briefing come from the pack's `briefing.md`; the advisor sections (`brain/advisor.md`) name no game at all.
7. **One name for one thing.** The words players already see: Tycho, the brain, the Tile, key actions, push-to-talk, look-up, game pack, "confirm", "undo". Don't introduce a second word for any of them.
8. **Honest about limits.** Tycho says only what it's confident is true in the game, says so in a few words when it doesn't know, and never claims it pressed or did something it can't check. A fact from a source names the source once ("UEX has...").
9. **Calm, precise, dry.** No exclamation marks, no emoji, no cheerleading. British spelling in Tycho's own text ("colour", "licence").

### What a test or the eval checks

- `tests/test_wording.py` reads the source and fails when a game's word appears in a core line (rule 6). Its `KNOWN` list holds today's exceptions and only shrinks.
- The same test checks that a setting's description names a game only when it's that game's setting, and that every pack's eval cases refuse the other packs' games by name.
- The brain eval (`tools/bench/tycho_eval.py`) fails a reply that says another game's word (`never_say`, per pack), a line said with a press that runs past the file's `say_words`, and a reply that runs past its case's `max_words`. It already failed a reply that narrates itself, dodges the question or claims a press with keys off.

Tone the tests can't catch (rules 1, 3, 4, 5, 9) is for the author and the review.

## Where the words live

Worked through on 15 November 2026 (Tycho 0.7.0 plus the 0.8 changes so far).

| What the player gets | Where it lives | State |
| --- | --- | --- |
| **Spoken replies and say lines** | Written by the brain from its briefing: `brain/core.md` (keys on), `brain/advisor.md` (keys off), and the pack's `briefing.md` and `facts.md` | `advisor.md` names no game. Its voice rule now says to answer what was asked and stop, and never to name the game. `core.md` still has Star Citizen's examples: #421. |
| **Tool descriptions the brain reads** | `brain/rounds.py` (`run_actions`, `run_sequence`), `tile/cards.py` (`show_card`), `spoken_settings.py` (`settings`), `knowledge/web.py` (`web_search`), and each look-up in `games/star_citizen/` | Star Citizen's look-ups are its own. `show_card`, `settings` and the web search briefs go to every game with Star Citizen's examples: #421. |
| **What to call the player** | `address.py`, with the pack's `call_me` and the player's setting | "Commander" is Star Citizen's default only (#349). "as a crewmate would": #421. |
| **Filler lines** ("Checking the latest.", "Looking that up.") | `runtime/speaking.py` | Fine: no game's words. |
| **The confirm question** ("Eject? Say confirm.") | `safety.py` (the reasons), `runtime/planning.py` (the question), with the pack's `safety.toml` and the action's own name | Fine. |
| **Fixed spoken lines from the brain's rules** ("I looked that up online, so I won't press anything for it.", "That answer ran too long for me.") | `brain/rounds.py` | Fine. |
| **Why a request failed** (the log, the window, the action log) | `runtime/asking.py`, `runtime/session.py`, `brain/claude.py`, `brain/openrouter.py`, `voice/elevenlabs.py` | Each gives a cause. Not spoken: the player hears the not-done sound. |
| **What look-ups tell the brain when they miss** | `brain/lookups.py`, `games/star_citizen/*_lookup.py`, `knowledge/web.py` | Fine: each says what to do instead ("answer without it"). |
| **Settings: names and descriptions** (the window's Settings, `settings.toml`, the settings tool) | `settings.py`, `desktop/bridge/state.py` (labels), `desktop/bridge/settings_page.py` (tabs) | Fixed here: web search and the Tile's nudge no longer name Star Citizen or a ship; Star Citizen's own settings say so. The Tile's `ship` colour and card kinds: #422. |
| **Settings by voice** ("Voice at 40%. Say undo to put it back.") | `spoken_settings.py`, `tile/display.py` | Fine. |
| **The window's pages** | `desktop/app/app.js`, `index.html`, and the bridge modules in `desktop/bridge/` | Fixed here: the Commands search box. Setup's game step still assumes Star Citizen: the onboarding work, #364. The Tile's preview card: #422. |
| **The tray** | `desktop/menu.py`, `desktop/tray.py`, `desktop/shell.py` | Fine. |
| **The Tile's own cards** (questions, warnings, notices) | `tile/cards.py`, `tile/display.py`, `start/run.py` | Fine. |
| **`tycho check`** | `doctor.py`, `findings.py`, `brain_choice.py`, `voice/choice.py`, `stt/choice.py`, `ptt/conflicts.py`, and the game's part in `games/star_citizen/checks.py` | Fixed here: the focus check names the chosen game, and is skipped where Tycho presses nothing. |
| **The command line** (`tycho --help`, `key`, `bind`, `model`, `gpu`, `actions`, `sounds`, `credits`) | `cli/`, `credentials.py`, `start/run.py` | Fine. `--help` and `credits` call Tycho a Star Citizen co-pilot: the product's description, the maintainer's to change. |
| **Start-up and download messages** | `start/`, `stt/models.py`, `stt/cuda.py`, `audio/microphone.py`, `runtime/instance.py` | Fine. |
| **The player's own files** (comments Tycho writes into `commands.toml`, `fleet.toml`, `settings.toml`) and what's wrong in them | `desktop/commands.py`, `commands/matcher.py`, `settings.py`, `games/star_citizen/fleet.py` | Fine. |
| **A pack that won't load** | `packs/pack.py` | For pack authors, not players. Fine. |
| **The installer** | `packaging/tycho.iss` | Fine. The publisher line calls Tycho a Star Citizen co-pilot, as above. |
| **The agents' skills** | `plugins/tycho/skills/` | Star Citizen only so far; they change with the command line (`tests/test_agent_skills.py`). |

### Still to do

- #421: what the brain is sent in every game still carries Star Citizen's examples. It needs Star Citizen's eval, on the maintainer's PC.
- #422: the Tile's preview and colour settings assume a ship.
- #364: setup's game step (onboarding).
- #359: whether short stray utterances get a reply at all.
- Star Citizen's eval now has `say_words = 15` and `never_say`; they're first measured on its next run.
