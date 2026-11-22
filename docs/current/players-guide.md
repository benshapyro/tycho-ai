# Player's guide

Everything Tycho does, in detail. Start with the [README](../../README.md) to install it and say your first command; come here when you want to know more.

- [Using it](#using-it)
- [Settings and logs](#settings-and-logs)
- [Install from source](#install-from-source)

## Using it

- **Hold push-to-talk, speak, let go.** Tycho only presses keys while Star Citizen is the window in front.
- **Say it your way.** "Gear down", "put the wheels out", "flight ready and request landing" and "take us down to the pad" all work. The built-in commands (`src/tycho/packs/star-citizen/commands.toml`) are instant; anything else goes to Claude, which takes about 1.3 seconds.
- **Sounds:** a click when the keys were pressed, a low tone when they weren't, two rising notes for a question. `uv run tycho sounds` plays them. Tycho can't see the game, so a click means the keys went in, not that the game acted on them: some actions need something else first (engine power needs the engines on, a quantum jump needs a route).
- **Confirm or cancel.** Ejecting, firing and anything else you can't undo asks first ("Eject? Say confirm."). Hold push-to-talk and say "confirm" within 5 seconds, or "cancel". Anything else you say drops the question.
- **The Tile.** When an answer has more than a few numbers or steps, Tycho says the gist and puts the rest on the Tile, in the top-right corner, where it fades by itself. Say "keep that up" to keep a card, "clear" to take it down, "show that again" to bring a kept one back, and "show me that" to read Tycho's last answer. Missed what Tycho said? "Say that again" repeats its last answer word for word, Tile on or off. A small light shows while you talk (green) and while Tycho thinks (your ship's colour). The Tile is a window of its own over the game, never anything inside it: clicks go through it and it never takes focus. Use Borderless Window mode (exclusive fullscreen hides it). Change it by voice: "make the Tile bigger", "move the Tile to the top left", "Tile more see-through", "keep the Tile orange", "Tile quiet mode", "hide the Tile".
- **First run.** On a fresh install, `tycho run` opens Tycho's window to set up, in five steps. You pick your game first, and Tycho says what it does there and finds the game's folder if it needs one (or you choose it). Then you add your keys (each with what it costs); press the button you want for push-to-talk (it warns if the game uses it too), test the microphone and choose Tycho's voice; see what leaves your PC and answer the crash reports question; and try it. Try it is a real test: hold your button and speak, and the window shows what Tycho heard and the keys it would press, while nothing reaches the game. Any step can be skipped and finished later; the last one lists what Tycho still needs. Tycho starts listening when you press Start. Everything you chose there can be changed later in Settings, or with Run setup again on Home.
- **The tray icon.** While Tycho runs, its star sits by the clock. Click it to open Tycho's window. Its places are down the left: Home (whether Tycho is ready and listening, which button to hold, the one thing to fix if there is one, the same checks as `tycho check`, and the last 20 things you said with what Tycho did, kept in memory only), What to say (everything you can say, your key for each, which have no key and what to bind, and your own phrases, added or removed there), The Tile (its settings and a preview), and Settings (every other setting, with how to say it, and your keys, stored in Windows Credential Manager). The game you chose sits under them, with Switch game. Right-click it to pause listening (push-to-talk does nothing until you resume), show or hide the Tile, open the logs folder, or quit. `tycho run --no-window` runs in the terminal only, as before.
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
  To give a built-in command more ways to say it, without replacing it (What to say in Tycho's window writes these for you):
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

Settings are in `%APPDATA%\tycho\settings.toml`. You can also change them by voice: "voice at 40%", "sounds a bit quieter", "give me less time to confirm", "stop keeping transcripts", "switch to PTU", "call me Ace", "stop calling me Commander". Volumes, the confirm time, what Tycho calls you and the Tile's settings change at once; the rest apply when Tycho next starts, and Tycho says which. "Undo" puts back the last change. A misheard sentence must never weaken a safeguard or your privacy, so by voice the confirm time only gets shorter, and transcripts, web search, the update check, crash reports and key actions only switch off: making them longer, or switching them on, is done in the file. With `key_actions` off, Tycho is an advisor: it answers, looks things up and shows cards, and presses nothing, whatever you or Claude say; a game whose rules forbid tools that press keys is always that way. The push-to-talk button (`tycho bind`), the microphone and the game folder never change by voice. If the voice says your name wrong, set `call_me_says` to how it sounds (`Zile` for `Xyl`); the console and logs keep your spelling. Claude runs on your Anthropic key unless you set `brain_provider = "openrouter"`, which runs the same Claude Haiku through your OpenRouter key (`tycho key set openrouter`); only models that passed Tycho's test with no wrong key presses can be picked (`brain_model`), and the brain's provider never changes by voice. Tycho's voice is George unless you set `voice` (George, Sarah, Lily, Brian, or a voice ID from your ElevenLabs account); a voice you designed works only with your own key, and Tycho checks at start and speaks as George if your key can't use it.

Logs are in `%LOCALAPPDATA%\tycho\logs`: `tycho.jsonl` for everything, `actions.jsonl` for every key Tycho pressed. Set `keep_transcripts = true` to also log what you said and what Tycho said, which helps when something goes wrong. It stays on your PC. Set `TYCHO_MODELS` to keep the speech model on another drive. Speech recognition runs on the processor; `transcription_device = "gpu"` moves it to an NVIDIA graphics card, which is faster but takes about 3 GB of the card's memory from the game. When it starts, Tycho asks GitHub whether a newer version is out and says so (it never updates itself); set `check_for_updates = false` to turn that off.

## Install from source

For developers, or to run the newest code. Most players should use the installer in the [README](../../README.md#install).

1. **Install uv**, which fetches Python for you. In PowerShell:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Then close and reopen PowerShell.
2. **Get Tycho.** Either `git clone https://github.com/benshapyro/tycho-ai`, or download the ZIP from [the repository page](https://github.com/benshapyro/tycho-ai) (Code, Download ZIP) and unzip it. Then `cd tycho-ai`.
3. **Install its parts.**
   ```powershell
   uv sync --no-group gpu   # speech recognition runs on the processor, so the game keeps the graphics card
   uv sync                  # only if you'll move speech to an NVIDIA card (adds about 2 GB of CUDA libraries)
   ```
   To move speech to the card later, set `transcription_device = "gpu"` in `settings.toml`, then `uv run tycho gpu install` adds the CUDA libraries (a 1.2 GB download, kept in `%LOCALAPPDATA%\tycho\cuda`); `uv run tycho gpu remove` takes them away.
4. **Add your keys** (optional, but most of Tycho needs them; see [Keys and costs](../../README.md#keys-and-costs)). Each command asks for the key and stores it in Windows Credential Manager, never in a file:
   ```powershell
   uv run tycho key set anthropic
   uv run tycho key set elevenlabs
   ```
5. **Check, choose a button, and go.**
   ```powershell
   uv run tycho check   # finds the game, reads your keybinds, tests the microphone and keys
   uv run tycho bind    # press the button you'll hold to talk (a mouse side button works well)
   uv run tycho run     # downloads the speech model the first time (0.7 GB)
   ```

Tycho finds the game where the RSI Launcher last installed or launched it, in the default folder, or in common folders on each drive. If it still can't, `tycho check` asks for the folder and saves it as `game_root` in `%APPDATA%\tycho\settings.toml`. To use PTU's keybinds instead of LIVE's, set `game_channel = "PTU"` there (or EPTU, TECH-PREVIEW, HOTFIX). `uv run tycho check` explains anything else that's wrong, in plain words.
