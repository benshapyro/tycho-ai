# 16. The Tile: a panel over the game, drawn by Tycho

Date: 2026-11-05 · Status: accepted; the in-game checks are still to do ([#2](https://github.com/benshapyro/tycho-ai/issues/2))

## Context

Some answers are better read than heard: three sell prices, a route, a recipe, the exact keybinding to set. Players asked for a small panel over the game, like Discord's overlay. Discord's overlay draws inside the game's renderer, which Tycho must never do ([ADR 3](0003-staying-within-cig-rules.md)).

The look was settled with the maintainer in a clickable mockup (4 November 2026, decisions in #2): cockpit-native, never official; compact; an accent that follows the ship; a fixed kit of cards. A desktop test (`tools/spike/tile/`, #115) checked the window itself with two engines, tkinter and Qt WebEngine.

## Decision

**A separate window, never anything in the game.** It stays on top and is see-through. Clicks pass through it, and it never takes focus, because Tycho presses keys only while the game has focus. The Win32 styles are TOPMOST, TRANSPARENT, LAYERED, TOOLWINDOW (no taskbar entry) and NOACTIVATE. It needs Borderless Window mode, not exclusive fullscreen.

**Tycho draws it itself.** Each card is painted with Pillow into a 32-bit image with per-pixel alpha, and put on screen with `UpdateLayeredWindow`. The maintainer chose this on 5 November 2026 over two alternatives:

| | Draw it ourselves (chosen) | Qt WebEngine | Tauri |
|---|---|---|---|
| Licence | Pillow is MIT-CMU; the rest is Windows | LGPL, which the build refuses (#56) | MIT/Apache |
| Added to the download | about 7 MB | about 300 MB | a Rust sidecar |
| Memory while playing | tens of MB | about 320 MB (measured) | 70–160 MB (reported) |
| See-through, soft edges | yes | yes | open bugs: see-through windows turning black when click-through changes |
| Reuses the HTML mockup | no: rebuilt in Python | almost as-is | almost as-is |

pywebview was ruled out earlier: its docs say transparent windows aren't supported on Windows. The setup app ([#12](https://github.com/benshapyro/tycho-ai/issues/12)) needn't be see-through, so it can still use web technology; it shares the design system's tokens, not the drawing code.

**One design system, as data.** `tycho.tile.design` holds the colours, fonts and sizes for both the Tile and the setup app. The core is a dark glass that never changes. Only the accent follows the ship, chosen by the manufacturer prefix in the ship's game name (`DRAK_` is Drake orange). Warning colours never follow the ship. Fonts are free (SIL OFL) with Windows fallbacks, and the Tile shows no logos or manufacturer marks. The accents are estimates until checked in game ([research](../research/manufacturer-ui.md)).

**A fixed kit that Claude fills.** Claude never writes markup. It calls `show_card` with a kind and the words and numbers, about 50 tokens. Generating a layout per answer would add seconds.

| Claude's kinds | Tycho's own |
|---|---|
| list, route, recipe, ship, fleet, steps, headlines, warning | confirm (the gate's question), notice, long answer ("show me that") |

- **`show_card` is an act.** Like `run_actions`, it ends the request and carries the spoken line. So a card costs no extra call to Claude.
- **Nothing is pressed.** A card may follow a web search, though a press may not (ADR 15).
- **Untrusted text.** What Claude writes is cleaned (no control or direction characters, no markdown), cut to fit, and only ever drawn as text.
- **Strict schema.** The tool schema is strict. Strict schemas can't carry length limits, so `tycho.tile.cards` enforces them.
- **Only while the Tile is on.** Claude is offered the tool only then, so it never says "two more on the Tile" when there's no Tile.

**When to show anything: when the eye beats the ear.** Show a card for:
- more than about three items or numbers;
- exact text to find or type;
- steps to follow;
- something that stays true for a while;
- a question waiting on the player.

Built-in commands, one-line answers and small talk stay audio-only. The briefing says so, and eval cases check both ways (`tag = "tile"`).

**The rules live apart from the window.** `tycho.tile.board` decides what shows, so it is tested with no screen:
- **Fading:** cards fade by themselves, longer ones staying longer (6 to 20 s, times the player's "cards stay").
- **One at a time:** a new card replaces the last, except that a pinned card shrinks to a strip ("show that again").
- **Exceptions:** a confirm card lasts as long as its question, and a long answer is pinned.
- **The player chooses which kinds show.** Confirm always shows while the Tile is on. Quiet mode leaves confirm, warnings and notices.
- **Hiding a card never hides the answer:** it is still said out loud.

**Never claim to see the game.** Cards show what Tycho sent or looked up, never gauges it can't read.

## Consequences

- **More drawing code.** Cards are drawn in Python, so the 10 layouts and their small animations (fade, the talk light's pulse, the confirm countdown) are code to write and test. That includes tests of the drawn images.
- **Starting the window takes the foreground once.** The desktop test found this. The Tile records the window in front before it starts, and gives the foreground back.
- **Screen capture.** A plain screen copy leaves out layered windows. Some streaming or capture tools may not show the Tile.
- **Open until the next play session:**
  - Easy Anti-Cheat, though nothing touches the game;
  - staying on top in Borderless;
  - the HUD colours in each ship.

  The window test in `tools/spike/tile/` checks these.
- **Next steps:**
  - drawing the cards;
  - the window;
  - a `Display` boundary for the session (the talk light, the confirm card, "keep that up", "clear", "show me that");
  - the Tile settings, by voice like the rest (#111).
