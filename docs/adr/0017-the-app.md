# 17. The app: one Tycho, a tray icon and a window

Date: 2026-11-05 · Status: accepted, amended 2026-11-15 (see the end) ([#12](https://github.com/benshapyro/tycho-ai/issues/12), [#10](https://github.com/benshapyro/tycho-ai/issues/10))

## Context

Setting Tycho up means a terminal today: `tycho bind`, `tycho key set`, `settings.toml` by hand. ADR 5 gave settings one schema and three ways in (a file, voice, and a settings screen) and left the screen's technology open (#12). The maintainer approved a clickable mockup on 5 November 2026 ([mockup](https://claude.ai/artifact/YE76DV4n7sKBKDaKAztEYJ), private to the maintainer), and made these decisions:

- **One Tycho, with a tray icon.** Starting Tycho puts an icon by the clock. The window opens by itself on first run, and from the icon after that. Closing the window leaves Tycho listening, and changes apply at once.
- **A real window,** not a browser tab.
- **Sections across the top:** Setup (Status once setup is done), Settings, Commands. The listening light and push-to-talk button sit in the title bar.
- **The first version** has three parts:
  - **A guided first run:** game folder, push-to-talk, microphone, keys, then a "say gear down" test.
  - **Settings,** with the Tile's own page and a live preview.
  - **Commands and sequences.**

  Fleet and logs come later.
- **After setup the Setup tab becomes Status:** a health check with a fix for each problem. Everything chosen in setup can be changed in Settings too.
- **The model is chosen in the app or the file, never by voice** (#112).

## Decision

**The window is pywebview over Windows' own web view (WebView2).** The app doesn't need to be see-through, unlike the Tile (ADR 16), so it can be HTML and CSS in the Tile's design system.

Licences, all permissive:
- pywebview: BSD-3.
- pythonnet, clr-loader: MIT.
- proxy_tools, bottle: MIT.
- The WebView2 loader DLLs pywebview ships: Microsoft's BSD-style SDK licence.

The WebView2 runtime comes with Windows 11.

Measured on the maintainer's PC (5 November 2026, Python 3.14, pywebview 6.2.1):
- the window opens in about 1 s;
- JavaScript calls Python directly;
- the window costs about 285 MB while open (six Edge web view processes), all freed when it closes;
- opening and closing it again works as often as needed.

**No local server.** The page is loaded as HTML from memory, and the page calls Python through pywebview's bridge (`js_api`). Nothing listens on a port, so there is no server to protect. If one is ever needed, the security rules still apply: it binds to 127.0.0.1 and needs a token.

**The window closes when the player closes it.** It is closed, not hidden, so it costs no memory while they play. The tray icon reopens it.

**Threads.** pywebview must run on the main thread, so:
- **The main thread** hosts the window. It waits for "open the window", then runs it until it closes.
- **The voice loop** (asyncio: push-to-talk, speech, the brain, the keys) moves to a thread of its own. Nothing in it changes.
- **The tray icon** has its own thread and message loop, like the Tile's window. It's drawn with Win32 directly (`Shell_NotifyIconW`), because pystray is LGPL.
- **Quitting** (the tray's Quit, or Ctrl+C in the terminal) stops the voice loop the way Ctrl+C does now: it releases any key it's holding, then ends.

**The bridge is the only way in, and it is narrow.** The page calls named methods on one Python object:

| | What it does |
|---|---|
| Settings | the schema, the current values, set, undo |
| Status | the checks |
| Keys | store or remove |
| Push-to-talk | capture a button |
| Microphone | the list, and a level |
| Commands | the list; add or remove a phrase |
| Setup test | "would press" |

Every call checks its input as the voice tool does. A setting changed in the app goes through the same `settings.update` and the same live callbacks as one changed by voice.

The app may do what voice can't: lengthen the confirm time, turn transcripts on, change the model. But only in its controls, and the page shows which settings voice can only lower.

**Keys never come back out.** The page sends a key once. Python checks it with one cheap request to its provider and stores it with `tycho.credentials`. From then on the page only learns whether one is stored and its last four characters. No key is ever written to a file, a log or the page.

**The pages are built from data.**
- **Settings** come from `settings.json_schema()`, with the voice rules already in it: the "say it" hint, "by voice only off", "applies now" or "at restart". A new setting appears in the app with no new page.
- **The Tile page** draws its live preview with the same tokens as the Tile.
- **Commands** come from the command matcher and the player's keybinds, including which commands have no key.

**The look is Tycho's design system** (`tycho.tile.design`): one dark look, cut corners, condensed capitals. The fonts are Windows' own, as on the Tile (Bahnschrift, Cascadia Mono, Consolas), so the page fetches nothing and works offline. The mockup's free fonts (SIL OFL) can be bundled later.

## Consequences

- `tycho run` becomes the app: tray icon, window on first run. `tycho run --no-window` keeps today's terminal-only behaviour. The other commands (`bind`, `key`, `check`) stay for the terminal and for agents.
- The voice loop moving off the main thread is the biggest change. Ctrl+C, the single-instance lock and shutdown need tests to show they behave as before.
- The installer grows by pywebview, pythonnet and the WebView2 loader, a few MB. The notices tool must include the WebView2 SDK licence.
- On Windows 10 without the WebView2 runtime, the window can't open. Tycho says so, with a link to Microsoft's installer, and keeps working without the window.
- The setup test and the Status checks reuse what `tycho check` already knows (the game, the keybinds, the microphone, the keys), so the terminal and the app give the same answers.
- Built so far (6 November 2026):
  - the threads and the tray icon (#126);
  - the window, the bridge, Status (the same checks as `tycho check`), Settings from the schema with the Tile's preview drawn by the Tile itself, and Keys.
  - the first run (`tycho run` opens the window on setup when there's no push-to-talk or no game; Tycho starts when it's done), and push-to-talk, the microphone and the game folder changeable in Settings. The "say gear down" test waits for the voice loop to be running during setup.
  - the Commands page: every command and sequence with its phrases and the player's key, what to bind for one with no key, and the player's own phrases added and removed. A phrase for a built-in command goes in a `[[phrases]]` entry, which adds phrases without copying the command, so later releases' changes to it still reach the player (#134).
- Build order:
  1. the threads and the tray icon;
  2. the bridge and the first run;
  3. Status and Settings;
  4. Commands.

## Amendment, 15 November 2026: the window's second layout

A design review of the window ([review](https://claude.ai/artifact/1hNCZ1eqZTtqhsXVwRVzWz), private to the maintainer) walked every screen and journey for both game packs. The maintainer chose its direction B, "the co-pilot console", and approved a clickable mockup ([mockup](https://claude.ai/artifact/MRWXof7zjaGFKg3xUNbZdq), private to the maintainer). The technology, the threads, the narrow bridge and the rule that keys never come back out all stay. These parts of the decision change:

- **Places down the left, not sections across the top.** Home, What to say, The Tile, Settings. The game and the version sit under them. This replaces "Setup (Status once setup is done), Settings, Commands".
- **Home replaces Status.** It says in one line whether Tycho is listening and which button to hold, shows at most one thing to fix with its button, and lists the last few exchanges: what the player said and what Tycho did. The full list of checks stays one click away.
- **The last exchanges are shown from memory only.** The voice loop keeps the last 20 in memory and the bridge hands them to the page. They are never written to a file because of this, they go nowhere but the window, and they are gone when Tycho quits. Keeping them on disk is still only the `transcripts` setting. The bridge gains one row: *Recent: the last exchanges, read only*.
- **Setup is a full-window flow of five steps** (game, keys, talk and listen, privacy, try it) with no places beside it, and Back and Next always in view.
- **"Try it" is real.** The voice loop runs during the last step of setup, so the player holds their button, speaks, and sees what Tycho heard. This replaces the bridge's "would press" check as the test. While setup is open the loop runs in a try-it mode: every press is planned through the safety gate as usual and then reported as "would press", never sent to the game. Leaving setup starts Tycho normally.
- **Settings are plain groups** (Voice, Talking, Safety, Privacy, Keys, Updates) with the rest under Advanced. They are still built from `settings.json_schema()`; the schema gains the group and an "advanced" mark. "Applies at restart" becomes one line with a Restart now button, not a note on each row.
- **The fonts are bundled.** Saira, Saira Semi Condensed and Share Tech Mono (all SIL OFL) ship in the installer and are embedded in the page from memory, so the page still fetches nothing and works offline. The Tile draws with the same files. Their licence goes in the notices.
- **One accent colour per game pack.** A pack may name its accent in its data (ADR 18); the window and the Tile use it. The rest of the look is unchanged.

What does not change: one Tycho with a tray icon, a real window that is closed and not hidden, no local server, the bridge as the only way in, the model never chosen by voice.

Choosing which screen the Tile shows on ([#447](https://github.com/benshapyro/tycho-ai/issues/447)) belongs to ADR 16 and is amended there with that work.
