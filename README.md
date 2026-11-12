# Tycho

**An unofficial voice co-pilot for Star Citizen®.**

> Named after Tycho Brahe, who charted the stars with unmatched precision before the telescope existed.

Tycho turns natural speech into in-game actions, using your own keybinds. Hold push-to-talk and say what you want, the way you'd say it to a crewmate. Tycho presses the right keys, tells you what it did, and asks before doing anything you can't undo. It can also answer Star Citizen questions.

**Status: pre-alpha (0.6).** Common commands run instantly on your PC. Claude handles everything said differently, plus questions, and Tycho answers aloud with an ElevenLabs voice. Anything you can't undo (ejecting, firing) waits for you to say "confirm". Install it with the installer from [Releases](https://github.com/benshapyro/tycho-ai/releases/latest), or from source with a terminal and about 15 minutes.

## Works today

- **Instant built-in commands**, almost 90 of them, through your own keybinds. Add your own phrases and sequences.
- **Claude for anything said differently,** for multi-step requests, and for questions.
- **Spoken replies** in an ElevenLabs voice you choose (George, Sarah, Lily, Brian, or one of your own), plus sounds that tell you whether the keys went in. "Say that again" repeats the last thing it said.
- **Asks before anything you can't undo,** and waits for you to say "confirm". Push-to-talk stops it at any point. When a request is vague ("faster") it asks a quick question instead of guessing, and what you say to your squad on the same button ("break right") presses nothing.
- **Answers about the game:**
  - ships, from the current patch's game files;
  - trade prices and routes, live from UEX;
  - where to refine an ore, and where to buy a ship with aUEC, from UEX;
  - where to mine a resource, from the Star Citizen Wiki;
  - weapons, armor, components and crafting;
  - news and upcoming patches, from RSI's sites.
- **Knows your fleet,** so "which of my ships is best for mining?" is about your ships.
- **Settings by voice:** "turn your voice down a bit", "less time to confirm", "stop searching the web". "Undo" puts back the last change.
- **Calls you what you like:** "Commander" by default, or "call me Ace", or nothing at all. It uses the name sparingly.
- **The Tile:** a small see-through panel over the game for what's better read than heard: prices, routes, specs, recipes, steps, and the countdown when Tycho asks you to confirm. Its colour follows your ship. It needs Borderless Window mode.
- **Its own window and a tray icon:** a guided first run (the game, your push-to-talk button, a microphone test, your keys), Status, Settings, and Commands, where you see everything you can say and add your own phrases.
- **A log of every key it pressed.**
- **Speech recognition on your PC,** on the graphics card when it has room and on the processor otherwise.
- **An installer, an update notice, and setup by an AI agent.**

## Coming

These are plans, not promises. Each links its issue.

- **Your fleet and logs in Tycho's window** ([#12](https://github.com/benshapyro/tycho-ai/issues/12)).
- **Choose your model** ([#112](https://github.com/benshapyro/tycho-ai/issues/112)): OpenAI and other providers, once they pass Tycho's tests.
- **Learns from misses and fixes itself** ([#51](https://github.com/benshapyro/tycho-ai/issues/51)): offers to add phrases you rephrased, and repairs keybinds after a patch, always with your OK.
- **Reads the game's log** ([#30](https://github.com/benshapyro/tycho-ai/issues/30)): knows which ship you're in and where you are, without being told.
- **Looks at your screen when you ask** ([#31](https://github.com/benshapyro/tycho-ai/issues/31)): it captures only the game window and keeps nothing.
- **Callouts** ([#4](https://github.com/benshapyro/tycho-ai/issues/4)) and an **optional wake word** ([#3](https://github.com/benshapyro/tycho-ai/issues/3)).

## Set up with an AI agent

If you use an AI agent on your PC (Claude Code, Codex, Cursor, Gemini CLI or another), it can install and set up Tycho for you. It runs the installer and Tycho's checks, and fixes what they find. It opens a window when it needs you: to paste an API key, which it never sees, or to press your push-to-talk button.

**Any agent:** paste this.

> Set up Tycho, the Star Citizen voice co-pilot, on this PC. Follow https://raw.githubusercontent.com/benshapyro/tycho-ai/main/plugins/tycho/skills/tycho-setup/SKILL.md

**Claude Code:** install Tycho's skills once, then ask it to "set up Tycho", or to "add a voice command for space mode".

```
/plugin install tycho --marketplace benshapyro/tycho-ai
```

On older versions of Claude Code, that's `/plugin marketplace add benshapyro/tycho-ai`, then `/plugin install tycho@tycho`.

**Codex and others that read `~/.agents/skills`:** copy the folders in [plugins/tycho/skills](plugins/tycho/skills) there.

The skills are [tycho-setup](plugins/tycho/skills/tycho-setup/SKILL.md) (install, keys, button, downloads, first run) and [tycho-commands](plugins/tycho/skills/tycho-commands/SKILL.md) (your own phrases and sequences, and why Tycho didn't understand something). For the agent, `tycho check --json` lists what's left to do, `tycho model download` gets the speech model, and `tycho actions <words>` finds the game's action names.

## Install

**Installer (preview).** Download `TychoSetup-<version>.exe` from the [latest release](https://github.com/benshapyro/tycho-ai/releases/latest) and run it. (Every build on `main` also makes one: the [Build workflow](https://github.com/benshapyro/tycho-ai/actions/workflows/build.yml)'s `tycho-installer` artifact.) It installs for you only, with no administrator prompt, adds Tycho to the Start menu and puts `tycho` on your PATH. It isn't code-signed yet, so Windows SmartScreen will say it's from an unknown publisher: choose **More info**, then **Run anyway**. When it finishes, Tycho starts, and its window walks you through setup: the game, your push-to-talk button, the microphone and your keys. After that, open Tycho from the Start menu; opening it again while it runs brings up its window. Everything also works from a terminal (`tycho check`, `tycho key set anthropic`, `tycho bind`, `tycho run`). With an NVIDIA card, `tycho gpu install` lets speech recognition use it. Uninstalling keeps your settings, keys and the speech model.

**From source:**

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
   Installed without them, `uv run tycho gpu install` adds the same CUDA libraries later (a 1.2 GB download, kept in `%LOCALAPPDATA%\tycho\cuda`); `uv run tycho gpu remove` takes them away.
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

Tycho finds the game where the RSI Launcher last installed or launched it, in the default folder, or in common folders on each drive. If it still can't, `tycho check` asks for the folder and saves it as `game_root` in `%APPDATA%\tycho\settings.toml`. To use PTU's keybinds instead of LIVE's, set `game_channel = "PTU"` there (or EPTU, TECH-PREVIEW, HOTFIX). `uv run tycho check` explains anything else that's wrong, in plain words.

## Keys and costs

| Key | What it adds | What it costs (your own account) |
|---|---|---|
| None | The built-in commands, said the usual way, with a click or a tone for each | Free |
| [Anthropic](https://console.anthropic.com) (Claude) | Anything said differently, misheard words, two-part requests, and questions | About $0.0002 a request, plus about $0.002 each time Tycho starts. An evening of play is usually a few cents. |
| [OpenRouter](https://openrouter.ai) (instead of Anthropic) | The same Claude, through one OpenRouter key: set `brain_provider = "openrouter"`. Web search still needs an Anthropic key; without one, Tycho says it can't search. | About what Anthropic charges, on your OpenRouter credit |
| [ElevenLabs](https://elevenlabs.io) (voice) | Tycho speaks its answers, what it did, and its questions. The key needs text-to-speech permission. | About 0.5 credits a character: a chatty hour is about 4,000 credits. The free plan is non-commercial and requires attribution. |

Without an ElevenLabs key, Tycho's replies are shown in its window and it plays its sounds instead.

## Using it

- **Hold push-to-talk, speak, let go.** Tycho only presses keys while Star Citizen is the window in front.
- **Say it your way.** "Gear down", "put the wheels out", "flight ready and request landing" and "take us down to the pad" all work. The built-in commands (`src/tycho/packs/star-citizen/commands.toml`) are instant; anything else goes to Claude, which takes about 1.3 seconds.
- **Sounds:** a click when the keys were pressed, a low tone when they weren't, two rising notes for a question. `uv run tycho sounds` plays them. Tycho can't see the game, so a click means the keys went in, not that the game acted on them: some actions need something else first (engine power needs the engines on, a quantum jump needs a route).
- **Confirm or cancel.** Ejecting, firing and anything else you can't undo asks first ("Eject? Say confirm."). Hold push-to-talk and say "confirm" within 5 seconds, or "cancel". Anything else you say drops the question.
- **The Tile.** When an answer has more than a few numbers or steps, Tycho says the gist and puts the rest on the Tile, in the top-right corner, where it fades by itself. Say "keep that up" to keep a card, "clear" to take it down, "show that again" to bring a kept one back, and "show me that" to read Tycho's last answer. Missed what Tycho said? "Say that again" repeats its last answer word for word, Tile on or off. A small light shows while you talk (green) and while Tycho thinks (your ship's colour). The Tile is a window of its own over the game, never anything inside it: clicks go through it and it never takes focus. Use Borderless Window mode (exclusive fullscreen hides it). Change it by voice: "make the Tile bigger", "move the Tile to the top left", "Tile more see-through", "keep the Tile orange", "Tile quiet mode", "hide the Tile".
- **First run.** On a fresh install, `tycho run` opens Tycho's window to set up: it finds the game (or you choose its folder), you press the button you want for push-to-talk (it warns if the game uses it too), pick and test the microphone, and add your keys. Tycho starts listening when you press Start. Everything you chose there can be changed later in Settings, or with Run setup again on Status.
- **The tray icon.** While Tycho runs, its star sits by the clock. Click it to open Tycho's window: Status (the same checks as `tycho check`), Settings (every setting, with how to say it, and a preview of the Tile), your keys (stored in Windows Credential Manager), and Commands (everything you can say, your key for each, which have no key and what to bind, and your own phrases, added or removed there). Right-click it to pause listening (push-to-talk does nothing until you resume), show or hide the Tile, open the logs folder, or quit. `tycho run --no-window` runs in the terminal only, as before.
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
  To give a built-in command more ways to say it, without replacing it (the Commands page in Tycho's window writes these for you):
  ```toml
  [[phrases]]
  command = "landing_gear"
  say = ["wheels down", "drop the wheels"]
  ```
  A phrase you add there is taken away from the built-in command that had it. `uv run tycho check` says whether your file loads.

### Optional: give Tycho's actions their own keys

Many game actions have no key by default, so Tycho falls back to toggles, which can flip the wrong way. This builds a control profile with your current bindings plus keyboard keys for specific on and off actions. It only fills empty slots and never changes your live keybinds until you load the profile in the game's keybinding menu:

```powershell
uv run python tools/spike/make_layout.py --install
```

## Settings and logs

Settings are in `%APPDATA%\tycho\settings.toml`. You can also change them by voice: "voice at 40%", "sounds a bit quieter", "give me less time to confirm", "stop keeping transcripts", "switch to PTU", "call me Ace", "stop calling me Commander". Volumes, the confirm time, what Tycho calls you and the Tile's settings change at once; the rest apply when Tycho next starts, and Tycho says which. "Undo" puts back the last change. A misheard sentence must never weaken a safeguard or your privacy, so by voice the confirm time only gets shorter, and transcripts, web search, the update check and key actions only switch off: making them longer, or switching them on, is done in the file. With `key_actions` off, Tycho is an advisor: it answers, looks things up and shows cards, and presses nothing, whatever you or Claude say; a game whose rules forbid tools that press keys is always that way. The push-to-talk button (`tycho bind`), the microphone and the game folder never change by voice. If the voice says your name wrong, set `call_me_says` to how it sounds (`Zile` for `Xyl`); the console and logs keep your spelling. Claude runs on your Anthropic key unless you set `brain_provider = "openrouter"`, which runs the same Claude Haiku through your OpenRouter key (`tycho key set openrouter`); only models that passed Tycho's test with no wrong key presses can be picked (`brain_model`), and the brain's provider never changes by voice. Tycho's voice is George unless you set `voice` (George, Sarah, Lily, Brian, or a voice ID from your ElevenLabs account); a voice you designed works only with your own key, and Tycho checks at start and speaks as George if your key can't use it.

Logs are in `%LOCALAPPDATA%\tycho\logs`: `tycho.jsonl` for everything, `actions.jsonl` for every key Tycho pressed. Set `keep_transcripts = true` to also log what you said and what Tycho said, which helps when something goes wrong. It stays on your PC. Set `TYCHO_MODELS` to keep the speech model on another drive. When it starts, Tycho asks GitHub whether a newer version is out and says so (it never updates itself); set `check_for_updates = false` to turn that off.

## What leaves your PC

Your voice never does: speech recognition runs on your PC. Only text is sent, and only to services you set up with your own keys.

| Service | What it receives | When |
|---|---|---|
| Anthropic (Claude) | What you said (as text); your bound actions with their keys, your commands and sequences; and your last few requests with what Tycho did, if they were in the last 30 seconds | Requests the built-in commands don't handle, plus one warm-up request when Tycho starts |
| OpenRouter, instead of Anthropic | The same as Anthropic (Claude) above. OpenRouter forwards it to Anthropic, who runs the model, so both receive it | Only with `brain_provider = "openrouter"`, instead of sending it to Anthropic directly |
| ElevenLabs | The text Tycho speaks | When Tycho speaks |
| Star Citizen Wiki API, UEX Corp | Nothing about you: Tycho downloads their ship lists | On first run, and when a new patch goes live |
| Anthropic (web search) | A short search query Claude writes from your question | Questions about news, upcoming patches, events and new ships; off with `web_search = false`. With the `web_search_open` trial (off by default), when those sites have no clear answer, any website, and Anthropic reads up to two of the pages it finds (never Reddit's pages, only its search snippets). Always Anthropic directly, with your Anthropic key, even when the brain runs through OpenRouter |
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

Speech recognition uses NVIDIA's [Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) model (CC-BY-4.0), via [onnx-asr](https://github.com/istupakov/onnx-asr). Ship and vehicle data comes from the [Star Citizen Wiki API](https://api.star-citizen.wiki), downloaded to your PC once per patch; its in-game prices are reported by players to [UEX Corp](https://uexcorp.space), which also lists the ships still in concept and provides the commodity prices and trade routes. Web search answers can draw on [starcitizen.tools](https://starcitizen.tools) (CC BY-SA 4.0). `tycho credits` shows these in the app, and the installer includes them.

## Licence

[Apache-2.0](LICENSE).

---

This is an unofficial Star Citizen fan project, not affiliated with the Cloud Imperium group of companies. All content in this project not authored by its host or users is property of its respective owners. Star Citizen®, Roberts Space Industries® and Cloud Imperium® are registered trademarks of Cloud Imperium Rights LLC. Visit the official site at [robertsspaceindustries.com](https://robertsspaceindustries.com).
