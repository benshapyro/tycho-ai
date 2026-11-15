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

**When to show anything: when the eye beats the ear** (the evidence is in the [research note](../research/output-modality.md)). Show a card for:
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

- **Pillow (13.0) and what it builds in.** Its Windows wheel builds in FreeType, HarfBuzz, libpng, libjpeg-turbo, zlib-ng and a few image codecs, all permissive. FreeType is dual-licensed; Tycho uses it under the FreeType License (BSD-style, with a credit), not GPLv2, and Pillow's licence file with that credit goes into THIRD-PARTY-NOTICES. Raqm and FriBiDi (LGPL) aren't in the wheel. The fonts are Windows' own (Bahnschrift, Consolas); the mockup's free fonts (Saira, Share Tech Mono) can be bundled later.
- **Measured cost of drawing:** 7 to 15 ms for a new card, under 1 ms a frame after, since a card is drawn once and kept; only the timer bar, the countdown and the talk light are drawn again.
- **More drawing code.** Cards are drawn in Python, so the 10 layouts and their small animations (fade, the talk light's pulse, the confirm countdown) are code to write and test. That includes tests of the drawn images.
- **Starting the window takes the foreground once.** The desktop test found this. The Tile records the window in front before it starts, and gives the foreground back.
- **Screen capture.** A plain screen copy leaves out layered windows. Some streaming or capture tools may not show the Tile.
- **Open until the next play session:**
  - Easy Anti-Cheat, though nothing touches the game;
  - staying on top in Borderless;
  - the HUD colours in each ship.

  The window test in `tools/spike/tile/` checks these.
- **Built (5 November 2026):**
  - the drawing (`tycho.tile.draw`);
  - the window (`tycho.tile.window`);
  - the `Display` boundary in the session: the talk light, the confirm card, "keep that up", "clear", "show that again" and "show me that";
  - 13 Tile settings, each changeable by voice at once, with a sample card after a change.

  The colour follows the ship the player said they're flying, until the log reader (#30) knows it for itself. Still to come: reduce motion, and a pick from any colour.

## Amendment, 15 November 2026: choosing the Tile's screen

The maintainer asked whether a player can choose which screen the Tile shows on ([#447](https://github.com/benshapyro/tycho-ai/issues/447)). Until now there was no choice: the Tile went on the screen of whichever window was in front when it drew. With the game in front that is right. It is wrong for a player who wants the Tile on a second screen beside the game, and when something else is in front on another screen, because the Tile jumps there.

- **A `tile_screen` setting.** It is `game` (the default, what happened before) or a screen as Windows names its device (`\\.\DISPLAY2`). The window's Tile page lists the screens plugged in now: "Follow the game", then "Screen 1 (main), 2560 x 1440" and the rest, main first, then left to right. The corner, size and nudges apply to the chosen screen.
- **The device name, not a number, is saved.** It stays the same while the screen stays in the same port. The number shown is only for reading.
- **An unplugged screen doesn't lose the Tile.** It follows the game instead and says so once on the Tile. It goes back to the chosen screen once that screen is plugged in again.
- **Not by voice.** A device name means nothing spoken. The choice is made in the window, or in `settings.toml`.
- **The rules stay apart from the window.** Which screen to use is `tycho.tile.screens`, tested with no screen. Only listing the screens asks Windows (`EnumDisplayMonitors`), and the list is kept for 2 seconds because the Tile asks for a frame 15 times a second.

What does not change: a separate window, never anything in the game, clicks passing through, and never taking focus.
