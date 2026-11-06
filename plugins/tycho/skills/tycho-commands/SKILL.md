---
name: tycho-commands
description: Add or change the player's own voice commands for Tycho, the unofficial Star Citizen voice co-pilot, in %APPDATA%\tycho\commands.toml. That means phrases, game actions and multi-step sequences. Also works out why Tycho didn't understand or act on something the player said. Use when the user wants a new phrase or command, a sequence ("start up and request departure"), or says Tycho mishears or ignores them.
---

# The player's own commands for Tycho

Tycho matches common phrases on the player's PC and presses the game action for them instantly. Anything it can't match goes to Claude, if the player has an Anthropic key, which takes about 1.3 s. The player's own file adds to the built-in commands, or replaces them:

- **Where:** `%APPDATA%\tycho\commands.toml`. Create it if it isn't there.
- **The built-in list:** https://github.com/benshapyro/tycho-ai/blob/main/src/tycho/commands/commands.toml. Read it to see the format, and to see what already exists.
- **Taking a phrase:** a phrase in the player's file is taken away from the built-in command that had it.
- **More ways to say a built-in:** use a `[[phrases]]` entry (below), not a copy of the command. A copy replaces the built-in and stops getting Tycho's fixes to it.
- **Replacing a built-in:** an entry with a built-in's `id` replaces that command. Do this only to change what it presses.

`tycho` below means `& "$env:LOCALAPPDATA\Programs\Tycho\tycho.exe"` (installed) or `uv run tycho` (from source).

## Rules

- **Never weaken a safety check.**
  - Don't remove `confirm = true` from anything.
  - Don't add `accepted_risks`.
  - Don't edit anything in the game's folder.

  Actions that can't be undone (ejecting, self-destruct, firing) always ask "confirm" anyway: the safety gate decides that, whatever the file says.
- Keep the player's existing entries and comments. Add to the file; don't rewrite it.
- After every change, run `tycho check --quick --json` and read the "Your commands" findings. A mistake in the file stops the file loading. Tycho then starts with the built-in commands only, so fix it before the player plays.
- The change takes effect the next time Tycho starts.

## Find the game's action name

The game's names are like `v_master_mode_set_scm`. Look them up with:

```
tycho actions master mode scm
```

- **Output:** each line is `name | label | keys | action map`. It also lists Tycho's own commands said with those words, with their actions.
- **No match:** try the game's wording. The game says "Landing System", not "landing gear".
- **"no key":** the action has no key bound, and Tycho can only press bound actions. Tell the player to bind one in the game (Options, Keybindings), or pick another action.

## A command

```toml
[[command]]
id = "space_mode"                       # unique; lower case and underscores
say = ["space mode", "go to space mode"] # what the player says, lower case
actions = ["v_master_mode_set_scm"]      # game actions, best first
```

| Key | What it does |
|---|---|
| `say` | Phrases. Matching forgives filler ("please", "could you") and small mishearings, but never a word that changes the meaning: "on" never matches "off". Common mishearings can go here too, as extra phrases. |
| `actions` | The first action with a key is pressed. Put a specific action ("Open All Doors") before a toggle, which is the fallback: a toggle pressed blind can do the opposite of what was asked. |
| `confirm = true` | Tycho asks "…? Say confirm." first. Use it for anything the player wouldn't want by accident. |
| `announced = true` | The game says it's done itself (the ship's computer, or air traffic control), so Tycho stays quiet. |
| `setup = ["..."]` | Actions pressed first to put the game in a known mode. Each needs its own key; there are no fallbacks. |
| `settle_seconds` | How long to wait after `setup` (0 to a few seconds). |
| `requires = ["quantum_route_set"]` | Press nothing unless the game's log shows a quantum route is plotted. This is the only fact Tycho can check. |

## More ways to say a command

To give any command, built-in or the player's, more phrases without changing it:

```toml
[[phrases]]
command = "landing_gear"              # the command's id
say = ["wheels down", "drop the wheels"]
```

- **One per command:** add to its `say` rather than writing a second entry. Tycho's window writes these entries too.
- **Taken phrases:** like any phrase in the player's file, each is taken from the built-in command that had it.

## A sequence

A sequence is several commands said as one:

```toml
[[sequence]]
id = "full_startup"
say = ["full startup", "wake her up"]
steps = [
    "flight_ready",
    { command = "lights_on", wait = 1.0 },  # waits 1 s first; 0 to 10 s
    "request_departure",
]
```

- **Steps:** each step is the `id` of a command, built-in or the player's. A sequence can't contain another sequence.
- **Confirm and requires:** they work on a sequence too. Tycho asks once, then runs every step; `requires` checks before the first step.
- **Stopping:** pressing push-to-talk again stops a sequence part way, and every key is released.

## When Tycho didn't understand or act

1. **Find what Tycho heard.** Read the end of `%LOCALAPPDATA%\tycho\logs\tycho.jsonl`. Each request is a line starting `#<n>`, with what was matched and how it ended (`done`, `no_key`, `no_focus`, `refused`, `asking`, `empty`...).
   - What the player said is logged only if `keep_transcripts = true` is in `%APPDATA%\tycho\settings.toml`. Ask before turning that on: it keeps a record of what they say.
   - `actions.jsonl` beside it lists every key Tycho pressed.
2. **Act on what it says:**
   - **Misheard** (`heard "…"` differs from what they said): add the misheard words to the command's `say`.
   - **`no_key`:** the action has no key bound; see above.
   - **`no_focus`:** Star Citizen wasn't the window in front. Tycho presses keys only into the game.
   - **Went to Claude, or nothing happened:** add the player's wording to `say`, so it's matched on the PC, instantly.
   - **`empty` or "too short":** the push-to-talk button was released too soon, or the microphone heard nothing. `tycho check` tests the microphone.
3. Check the file again, and ask the player to try it after restarting Tycho.

## Their fleet

`%APPDATA%\tycho\fleet.toml` lists the player's ships, one name a line, as the game writes them. Tycho changes it when they say "I bought a …" or "I sold my …", and it's fine to edit by hand:

```toml
ships = [
    "Cutlass Black",
    "MISC Prospector",
]
```
