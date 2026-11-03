# Tycho

**An unofficial voice co-pilot for Star Citizen®.**

> Named after Tycho Brahe, who charted the stars with unmatched precision before the telescope existed.

Tycho turns natural speech into in-game actions, using your own keybinds. Hold push-to-talk and say what you want, the way you'd say it to a crewmate. Tycho presses the right keys, tells you what it did, and asks before doing anything you can't undo. It can also answer Star Citizen questions.

**Status: pre-alpha (0.3).** Common commands run instantly on your PC. Claude handles everything said differently, plus questions, and Tycho answers aloud with an ElevenLabs voice. Anything you can't undo (ejecting, firing) waits for you to say "confirm". There's no installer yet: you need a terminal and about 15 minutes.

## Install

1. **Install uv**, which fetches Python for you. In PowerShell:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Then close and reopen PowerShell.
2. **Get Tycho.** Either `git clone https://github.com/benshapyro/tycho-ai`, or download the ZIP from [the repository page](https://github.com/benshapyro/tycho-ai) (Code, Download ZIP) and unzip it. Then `cd tycho-ai`.
3. **Install its parts.**
   ```powershell
   uv sync                  # with an NVIDIA graphics card (adds about 2 GB of CUDA libraries)
   uv sync --no-group gpu   # without one; speech recognition then runs on the processor
   ```
4. **Add your keys** (optional, but most of Tycho needs them; see [Keys and costs](#keys-and-costs)). Each command asks for the key and stores it in Windows Credential Manager, never in a file:
   ```powershell
   uv run tycho key set anthropic
   uv run tycho key set elevenlabs
   ```
5. **Check, choose a button, and go.**
   ```powershell
   uv run tycho check   # finds the game, reads your keybinds, tests the microphone and keys
   uv run tycho bind    # press the button you'll hold to talk (a mouse side button works well)
   uv run tycho run     # downloads the speech model the first time (2.5 GB)
   ```

If the game isn't in `C:\Program Files\Roberts Space Industries\StarCitizen`, set `game_root` in `%APPDATA%\tycho\settings.toml` to the folder that contains `LIVE`. `uv run tycho check` explains anything else that's wrong, in plain words.

## Keys and costs

| Key | What it adds | What it costs (your own account) |
|---|---|---|
| None | The built-in commands, said the usual way, with a click or a tone for each | Free |
| [Anthropic](https://console.anthropic.com) (Claude) | Anything said differently, misheard words, two-part requests, and questions | About $0.0002 a request, plus about $0.002 each time Tycho starts. An evening of play is usually a few cents. |
| [ElevenLabs](https://elevenlabs.io) (voice) | Tycho speaks its answers, what it did, and its questions. The key needs text-to-speech permission. | About 0.5 credits a character: a chatty hour is about 4,000 credits. The free plan is non-commercial and requires attribution. |

Without an ElevenLabs key, Tycho's replies go to the log and it plays its sounds instead.

## Using it

- **Hold push-to-talk, speak, let go.** Tycho only presses keys while Star Citizen is the window in front.
- **Say it your way.** "Gear down", "put the wheels out", "flight ready and request landing" and "take us down to the pad" all work. The built-in commands (`src/tycho/commands/commands.toml`) are instant; anything else goes to Claude, which takes about 1.3 seconds.
- **Sounds:** a click when something was done, a low tone when it wasn't, two rising notes for a question. `uv run tycho sounds` plays them.
- **Confirm or cancel.** Ejecting, firing and anything else you can't undo asks first ("Eject? Say confirm."). Hold push-to-talk and say "confirm" within 5 seconds, or "cancel". Anything else you say drops the question.
- **Stop.** Pressing push-to-talk stops Tycho talking, and stops any key sequence it's in the middle of.
- **Questions.** Ask about ships, places or how something works. Ship facts come from the current patch's game files, and ships still in concept from UEX. Commodity prices and trade routes come live from UEX ("where's the best place to sell quantanium?", "best trade route from Lorville with 96 SCU?"). Weapons, armor, ship components and crafting recipes come from the game files, via the Star Citizen Wiki API ("what do I need to craft an FS-9?", "Bulwark or Palisade?"). News, upcoming patches and events come from a web search of RSI's sites and the community wiki: Tycho says "Let me check." and answers a few seconds later. Everything else comes from Claude's own knowledge, which can be out of date.
- **Your ships.** Tell Tycho what you own and what you're flying: "I own a Cutlass Black", "I just bought a Carrack", "I sold my Prospector", "I'm flying the Cutlass today". It then answers "what should I haul?" or "which of my ships is best for mining?" with your ships. Your fleet is kept in `%APPDATA%\tycho\fleet.toml`, which you can edit too; the ship you're flying lasts until you say otherwise or restart.
- **Discord on open mic?** Bind Discord's **Push to Mute** (Discord, User Settings, Keybinds) to the same button as Tycho's push-to-talk. While you hold it to talk to Tycho, your channel doesn't hear you, and nothing can get stuck muted. (Tycho can't mute Discord for you: on some headsets, muting one app's microphone mutes it for every app, Tycho included. See [#14](https://github.com/benshapyro/tycho-ai/issues/14).)
- **Your own words.** Add phrases, commands and sequences in `%APPDATA%\tycho\commands.toml`. For example:
  ```toml
  [[command]]
  id = "space_mode"
  say = ["space mode"]
  actions = ["v_master_mode_set_scm"]
  ```
  A phrase you add there is taken away from the built-in command that had it. `uv run tycho check` says whether your file loads.

### Optional: give Tycho's actions their own keys

Many game actions have no key by default, so Tycho falls back to toggles, which can flip the wrong way. This builds a control profile with your current bindings plus keyboard keys for specific on and off actions. It only fills empty slots and never changes your live keybinds until you load the profile in the game's keybinding menu:

```powershell
uv run python tools/spike/make_layout.py --install
```

## Settings and logs

Settings are in `%APPDATA%\tycho\settings.toml`, and logs in `%LOCALAPPDATA%\tycho\logs`: `tycho.jsonl` for everything, `actions.jsonl` for every key Tycho pressed. Set `keep_transcripts = true` to also log what you said and what Tycho said, which helps when something goes wrong. It stays on your PC. Set `TYCHO_MODELS` to keep the speech model on another drive.

## What leaves your PC

Your voice never does: speech recognition runs on your PC. Only text is sent, and only to services you set up with your own keys.

| Service | What it receives | When |
|---|---|---|
| Anthropic (Claude) | What you said (as text); your bound actions with their keys, your commands and sequences; and your last few requests with what Tycho did, if they were in the last 30 seconds | Requests the built-in commands don't handle, plus one warm-up request when Tycho starts |
| ElevenLabs | The text Tycho speaks | When Tycho speaks |
| Star Citizen Wiki API, UEX Corp | Nothing about you: Tycho downloads their ship lists | On first run, and when a new patch goes live |
| Anthropic (web search) | A short search query Claude writes from your question | Questions about news, upcoming patches, events and new ships; off with `web_search = false` |
| UEX Corp | Which commodity or trading place you asked about | Trade questions |
| Star Citizen Wiki API | The item names you ask about | Weapon, armor, component and crafting questions |
| *Planned:* Anthropic (Claude) | A capture of the game window | Only if you turn vision on and ask Tycho to look |

## Principles

- **Receipts, not claims.** Every action is logged: what was pressed, and whether you confirmed it.
- **Fast where it matters.** Known commands skip the AI entirely.
- **Asks before the irreversible.** The safety gate is separate from the AI: nothing Claude picks can skip a confirmation.
- **Plays by the rules.** It sends keypresses through your keybinds only. It never reads game memory or injects into the game. Every action starts from you; there's no unattended automation.
- **Your screen is yours** (planned): vision will be off unless you ask, capture only the game window, and keep nothing by default.
- **Measured, not claimed.** Accuracy and latency are benchmarked against a recorded test set.

## Development

```sh
uv sync
uv run pytest
uv run ruff check
uv run pyright
```

See [docs/architecture.md](docs/architecture.md) for the design, [docs/adr](docs/adr) for the decisions behind it, and [CONTRIBUTING.md](CONTRIBUTING.md) to get involved.

## Credits

Speech recognition uses NVIDIA's [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) model (CC-BY-4.0), via [onnx-asr](https://github.com/istupakov/onnx-asr). Ship and vehicle data comes from the [Star Citizen Wiki API](https://api.star-citizen.wiki), downloaded to your PC once per patch; its in-game prices are reported by players to [UEX Corp](https://uexcorp.space), which also lists the ships still in concept and provides the commodity prices and trade routes. Planned look-ups will also use [starcitizen.tools](https://starcitizen.tools) (CC BY-SA 4.0).

## Licence

[Apache-2.0](LICENSE).

---

This is an unofficial Star Citizen fan project, not affiliated with the Cloud Imperium group of companies. All content in this project not authored by its host or users is property of its respective owners. Star Citizen®, Roberts Space Industries® and Cloud Imperium® are registered trademarks of Cloud Imperium Rights LLC. Visit the official site at [robertsspaceindustries.com](https://robertsspaceindustries.com).
