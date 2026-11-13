---
name: tycho-setup
description: Install, set up or upgrade Tycho, the unofficial voice co-pilot for Star Citizen, on the user's Windows PC, then check it works. Use when the user asks to install Tycho, set it up, get it working, upgrade it, or fix what `tycho check` reports. Covers the silent install from GitHub Releases, API keys (entered by the user in their own window, never seen by you), the push-to-talk button, the speech model and graphics card downloads, and the first run.
---

# Set up Tycho for the user

Tycho turns speech into Star Citizen key presses using the player's own keybinds, and answers game questions. It runs on Windows only. You install it, run its checks, and fix what they report. The user does three things themselves: enter API keys, press their push-to-talk button, and play.

Repository: https://github.com/benshapyro/tycho-ai. README: https://github.com/benshapyro/tycho-ai#readme.

## Rules

- **Never see, ask for or handle an API key.** Not in chat, not in a file, not in an environment variable, not piped into a command. `tycho key set <name>` refuses piped input on purpose. Keys are entered by the user in a console window you open for them (step 3), and live only in Windows Credential Manager.
- **Never touch the game's files.** Don't edit anything in the Star Citizen folder, `actionmaps.xml` included. Tycho only reads the game.
- **Don't run `tycho run` in your own shell.** It listens to the microphone and presses keys. Start it in a window for the user (step 6), and only when they say they're ready.
- **Ask before downloading a lot**: the speech model is 2.5 GB, and the graphics card libraries are 1.2 GB. Say the size and where it goes.
- **Ask before installing or upgrading.** Installing is per-user (no administrator prompt) into `%LOCALAPPDATA%\Programs\Tycho`.
- Prefer your structured question tool, if you have one, for the user's choices.

The commands below are PowerShell. From a POSIX shell (for example Git Bash), run each through `powershell -NoProfile -Command "..."`.

## 1. Look at what's there

```powershell
if ($env:OS -ne "Windows_NT") { "Tycho runs on Windows only." }
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
if (Test-Path $tycho) { & $tycho --version } else { "Tycho isn't installed." }
Get-Process tycho, tycho-app -ErrorAction SilentlyContinue
```

Each snippet here stands alone and sets `$tycho` itself, because your shell may not keep variables between commands. Always use the full path: the installer adds Tycho to the user's PATH, but only shells started afterwards see it.

If Tycho is running, ask the user to quit it before you install or upgrade: right-click its star by the clock and choose Quit Tycho (closing its window leaves it running), or press Ctrl+C in its terminal. Check again that it's gone. The installer can't replace a running Tycho's files, and a silent install stops without installing if it finds one. Don't end the process yourself: quitting lets Tycho release any key it's holding.

## 2. Install, or upgrade, from the latest release

```powershell
$release = Invoke-RestMethod "https://api.github.com/repos/benshapyro/tycho-ai/releases/latest"
$asset = $release.assets | Where-Object name -like "TychoSetup-*.exe" | Select-Object -First 1
$setup = Join-Path $env:TEMP $asset.name
Invoke-WebRequest $asset.browser_download_url -OutFile $setup
(Get-FileHash $setup -Algorithm SHA256).Hash
```

Run that in one go: it needs `$release`. Compare the hash with the `SHA-256` line in the release notes (`$release.body`, or the release page). If it differs, stop, delete the file, and tell the user.

Then install it silently (set `$setup` again if your shell didn't keep it: `Join-Path $env:TEMP <the file name>`):

```powershell
Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-" -Wait
& "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe" --version
```

- A file downloaded this way has no "downloaded from the internet" mark, so Windows SmartScreen doesn't stop it.
- If the user downloaded it in a browser, SmartScreen says "unknown publisher" because the installer isn't code-signed yet. They choose **More info**, then **Run anyway**.
- An upgrade keeps the user's settings, keys, commands and speech model.

`tycho check --json`, `tycho model download` and `tycho actions` need Tycho 0.3.1 or newer. With an older version, upgrade first.

## 3. Check, and fix what it reports

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
& $tycho check --quick --json
```

The output is JSON: `ready` (false while anything fails) and `findings`, each with `level` (`ok`, `warn` or `fail`), `what`, `detail` and `fix` (the command or change that fixes it, or null). Work through every `fail`, then every `warn`, as below. Run the check again after each fix.

### API keys (findings "Brain" and "Voice")

Both keys are optional. Explain what each adds, and ask which the user wants:

- **Anthropic** (Claude) understands anything said differently from the built-in phrases, and answers questions. It costs about $0.0002 a request; an evening of play is usually a few cents. Get a key at https://console.anthropic.com.
- **ElevenLabs** gives Tycho a voice. A chatty hour is about 4,000 credits, and the free plan is non-commercial. Get a key at https://elevenlabs.io; it needs text-to-speech permission. Without it, replies are shown in Tycho's window.

For each key the user wants, open a window where they paste it. You never see it:

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
Start-Process cmd -ArgumentList "/k", "`"`"$tycho`" key set anthropic`""    # or: elevenlabs, openrouter
```

Tell the user: "In the window that opened, paste your key (it stays hidden) and press Enter, then close the window." When they say it's done, confirm it:

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
& $tycho key check anthropic    # prints a masked key, e.g. sk-ant-...AB12, and exits 0
```

If it says "Stored, but unusable", the key was pasted with a hidden character: open the `key set` window again and have the user copy the key afresh.

### Push-to-talk (finding "Push-to-talk")

The user picks the button they'll hold to talk. A mouse side button works well, and a joystick button is fine too. Open a window for it:

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
Start-Process cmd -ArgumentList "/k", "`"`"$tycho`" bind`""
```

Tell the user to press and release the button within 30 seconds, then close the window. If a finding named "Push-to-talk conflict" says the button does something in the game, suggest another button, or unbinding it in the game.

### Star Citizen not found (finding "Star Citizen")

Ask where the game is installed. The folder they give holds `Data.p4k`, for example `C:\Program Files\Roberts Space Industries\StarCitizen\LIVE`. Add it to `%APPDATA%\tycho\settings.toml`, keeping any lines already there:

```toml
game_root = 'D:\Games\StarCitizen\LIVE'
```

Use single quotes, so the backslashes are kept as written. To use PTU's keybinds instead of LIVE's, add `game_channel = "PTU"`.

### Speech model (finding "Speech model")

Ask first, since it's 2.5 GB into `%LOCALAPPDATA%\tycho\models`. To put it on another drive, set the user environment variable `TYCHO_MODELS` to a folder there before downloading.

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
& $tycho model download
```

This takes a few minutes. If the connection drops, run it again and it continues where it stopped.

### Graphics card (finding "Speech recognition")

If its `fix` is `tycho gpu install`, the user's NVIDIA card can recognise speech about 10x faster after a one-time 1.2 GB download. Ask, then run:

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
& $tycho gpu install
```

If the detail says to update the NVIDIA driver, tell the user; don't update it yourself. With no NVIDIA card, speech recognition runs on the processor, which is fine.

### Anything else

Follow the finding's `detail`, which says what's wrong in plain words. The "Settings" finding names an unknown setting, usually a typo. "Your commands" means the user's `commands.toml` has a mistake; the `tycho-commands` skill covers that file.

## 4. The checks that need the user

When the quick check is ready, run the full check in a window, because it needs the user:

```powershell
$tycho = "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"
Start-Process cmd -ArgumentList "/k", "`"`"$tycho`" check`""
```

Tell them it listens to the microphone for a few seconds (they should say something), then asks them to switch to Star Citizen within 10 seconds (the game should be running). Ask them what it said, or what it put at the end.

- A microphone that hears nothing is usually muted, or Windows is blocking desktop apps from it (Settings, Privacy & security, Microphone).
- To use a microphone other than the Windows default, set `microphone = "<part of its name>"` in `settings.toml`.

## 5. Optional: Discord, and the user's own words

- **Discord on open mic.** Suggest binding Discord's **Push to Mute** (Discord, User Settings, Keybinds) to the same button as Tycho's push-to-talk, so their channel doesn't hear what they say to Tycho.
- **Their own phrases and sequences.** Offer to add them with the `tycho-commands` skill.

## 6. First run

When the user is ready, start Tycho. It has no terminal: its star appears by the clock (or under the ^ arrow there), and its window opens on setup if anything is still missing:

```powershell
Start-Process "$env:LOCALAPPDATA\Programs\Tycho\tycho-app.exe"
```

From then on they start it from Start, **Tycho**. Opening it again while it runs brings up its window, as does clicking the star. Tell them how it works:

- Hold the button, speak, and let go: "landing gear", "request landing", "flight ready". Questions work too, with a key: "best place to sell quantanium?".
- Tycho presses keys only while Star Citizen is the window in front.
- **Sounds:** a click means the keys were pressed, a low tone means they weren't, and two rising notes mean a question. Tycho can't see the game, so a click doesn't prove the game acted: some actions need something else first, like engine power needing the engines on.
- Anything that can't be undone (ejecting, firing) waits for them to hold the button and say "confirm".
- Pressing the button again stops whatever Tycho is doing.

## 7. Report back

Give the user a short summary:

- what was installed, and the version;
- which keys are set (by name only);
- the push-to-talk button;
- where speech recognition runs;
- anything left to do.

## Where things are

| What | Where |
|---|---|
| The app | `%LOCALAPPDATA%\Programs\Tycho` |
| Settings, the player's commands and fleet | `%APPDATA%\tycho\` (`settings.toml`, `commands.toml`, `fleet.toml`) |
| Logs | `%LOCALAPPDATA%\tycho\logs\tycho.jsonl` (everything), `actions.jsonl` (every key pressed) |
| Speech model and graphics card libraries | `%LOCALAPPDATA%\tycho\models`, `%LOCALAPPDATA%\tycho\cuda` |
| API keys | Windows Credential Manager, under "tycho" |

- **To uninstall,** use Windows Settings, Apps, Tycho. That keeps the settings, keys and speech model. `tycho key delete <name>` removes a key, and `tycho gpu remove` the graphics card libraries.
- **To install from source instead** (for development), follow the README's "From source" steps; every command becomes `uv run tycho ...`.
