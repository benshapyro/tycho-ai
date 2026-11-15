# Tycho on the desktop (ADR 17)

One Tycho process: the voice loop on its own thread (`host.py`), the tray icon on its own (`tray.py`), and the app window on the main thread (`window.py`, pywebview over WebView2). Read the root AGENTS.md first.

## Rules

- **The page's only way into Python is the `bridge` package**: one `Bridge` object, its calls in a module per page (`settings_page`, `setup_page`, `commands_page`, `updates_page`, `tile_page`) over the shared `state`. Every public name on it is a call the page can make, and `tests/test_bridge_pages.py` checks they're exactly the ones the page uses. It checks everything it's given. Keys go in and never come out: no method returns a key.
- **No local server.** The page loads from memory; pywebview's `js_api` is the bridge.
- **The window must run on the main thread**, so the voice loop runs in `Host`. Live setting changes reach the loop through `Host.run_now`.
- **pystray is LGPL:** the tray is our own Win32 code. Never add an LGPL or GPL dependency.
- **Two programs:** `tycho.exe` is for the terminal (the commands, and agents), and `tycho-app.exe` (the Start menu) has no console. In the app, `tycho.start.run.WINDOWED` is True (`desktop/shell.py` is handed it as `windowed`): never prompt in a terminal. A problem shows in a message box (`packaging/tycho_main.py`). Subprocesses use `CREATE_NO_WINDOW`.
- **The tray icon or the Tile stopping is said once, and Tycho keeps listening** (#297). Each calls its `on_stopped` on its own thread; `Session.tell` takes it to the voice loop: a notice card if the Tile can show one, otherwise spoken. An exception inside a window procedure never reaches Python's caller (ctypes drops it), so catch it there, as `TileWindow._tick` does.
- **A second start shows the running Tycho's window.** It uses a named event (`runtime/instance.py`, `OpenRequests`), not a server, and the window is raised once shown.
- **The Commands page edits the player's `commands.toml` with tomlkit, so their comments stay.** A change is written only if the file then loads (`CommandMatcher.default`). Added phrases go in `[[phrases]]` entries; never copy a built-in command.

- **The page's words follow `docs/voice-and-tone.md`.** The window shows whichever game is chosen, so a label or hint names no game: take the game's name from the bridge's data.

## Testing

- `tests/fakes/fake_setup_pc.py` and `fake_key_check.py` stand in for the PC and the key checks.
- The page can be driven in a browser with a `window.TYCHO_FAKE` bridge (see the app tests).
- Windows-only behaviour (the tray, the foreground, the windowless app) needs the real PC: install CI's build, and check it with the maintainer at the screen.
