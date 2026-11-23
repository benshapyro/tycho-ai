# Tycho on the desktop (ADR 17)

One Tycho process: the voice loop on its own thread (`host.py`), the tray icon on its own (`tray.py`), and the app window on the main thread (`window.py`, pywebview over WebView2). Read the root AGENTS.md first.

## Rules

- **The page's only way into Python is the `bridge` package**: one `Bridge` object, its calls in a module per page (`settings_page`, `setup_page`, `commands_page`, `updates_page`, `tile_page`, `memory_page`, `history_page`) over the shared `state`. Every public name on it is a call the page can make, and `tests/test_bridge_pages.py` checks they're exactly the ones the page uses. It checks everything it's given. Keys go in and never come out: no method returns a key.
- **No local server.** The page loads from memory; pywebview's `js_api` is the bridge.
- **The window must run on the main thread**, so the voice loop runs in `Host`. Live setting changes reach the loop through `Host.run_now`.
- **pystray is LGPL:** the tray is our own Win32 code. Never add an LGPL or GPL dependency.
- **Two programs:** `tycho.exe` is for the terminal (the commands, and agents), and `tycho-app.exe` (the Start menu) has no console. In the app, `tycho.start.run.WINDOWED` is True (`desktop/shell.py` is handed it as `windowed`): never prompt in a terminal. A problem shows in a message box (`packaging/tycho_main.py`). Subprocesses use `CREATE_NO_WINDOW`.
- **The tray icon or the Tile stopping is said once, and Tycho keeps listening** (#297). Each calls its `on_stopped` on its own thread; `Session.tell` takes it to the voice loop: a notice card if the Tile can show one, otherwise spoken after the line being said (`Telling` in `runtime/speaking.py`; it never cuts speech off, and what's told before the loop runs waits for it). A stopped Tile is greyed out on the tray menu. An exception inside a window procedure never reaches Python's caller (ctypes drops it), so catch it there, as `TileWindow._tick` does.
- **Every way Tycho ends cleanly calls `crashes.stop()`** (#428), or the next start says it crashed: `atexit`, the starting star's `os._exit`, Windows shutting down or signing out (`WM_ENDSESSION` in `tray.py`, `on_end_session`), and the console closing (`CloseGuard`'s `ending`). Neither of the last two runs `atexit`.
- **Quit is in the window too** (at the foot of the places, #486, and Settings, Updates; `Bridge.quit`, #424): the tray icon can be hidden or gone. It asks the `Host` for `quit`, as the tray's menu does.
- **A second start shows the running Tycho's window.** It uses a named event (`runtime/instance.py`, `OpenRequests`), not a server, and the window is raised once shown.
- **The History page (#484) reads the kept sessions fresh each time through `history.kept`**, which first deletes what's over 30 days old. A session is named by its start time; the bridge never takes a path. "Delete all" asks with a second click: there's no undo.
- **The Memory page (#483) lists both memory files' notes and deletes one through `memory.NoteFile`** (tomlkit, comments stay). The fleet and the name beside them are read-only there; they keep their own places in Settings.
- **The Commands page edits the player's `commands.toml` with tomlkit, so their comments stay.** A change is written only if the file then loads (`CommandMatcher.default`). Added phrases go in `[[phrases]]` entries; never copy a built-in command.

- **The page's words follow `docs/voice-and-tone.md`.** The window shows whichever game is chosen, so a label or hint names no game: take the game's name from the bridge's data.

## Testing

- `tests/fakes/fake_setup_pc.py` and `fake_key_check.py` stand in for the PC and the key checks.
- The page can be driven in a browser with a `window.TYCHO_FAKE` bridge. `tests/test_setup_page.py` does it for real with Playwright: the app's own page, every call going to a real `Bridge` over the fake PC. It uses the PC's own Edge on Windows (and CI), so there's no browser to download. A new step or page gets a test there.
- **Setup names no game** (#364). What it says about a game is that pack's `[setup]` text (`packs/onboarding.py`), sent by `setup_state` and `pack_setup`. Don't put a game's words in `app.js` or the bridge.
- Windows-only behaviour (the tray, the foreground, the windowless app) needs the real PC: install CI's build, and check it with the maintainer at the screen.
