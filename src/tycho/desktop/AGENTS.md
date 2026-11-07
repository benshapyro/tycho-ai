# Tycho on the desktop (ADR 17)

One Tycho process: the voice loop on its own thread (`host.py`), the tray icon on its own (`tray.py`), and the app window on the main thread (`window.py`, pywebview over WebView2). Read the root AGENTS.md first.

## Rules

- **The page's only way into Python is `bridge.py`.** It checks everything it's given. Keys go in and never come out: no method returns a key.
- **No local server.** The page loads from memory; pywebview's `js_api` is the bridge.
- **The window must run on the main thread**, so the voice loop runs in `Host`. Live setting changes reach the loop through `Host.run_now`.
- **pystray is LGPL:** the tray is our own Win32 code. Never add an LGPL or GPL dependency.
- **Two programs:** `tycho.exe` is for the terminal (the commands, and agents), and `tycho-app.exe` (the Start menu) has no console. In the app, `app.WINDOWED` is True: never prompt in a terminal. A problem shows in a message box (`packaging/tycho_main.py`). Subprocesses use `CREATE_NO_WINDOW`.
- **A second start shows the running Tycho's window.** It uses a named event (`runtime/instance.py`, `OpenRequests`), not a server, and the window is raised once shown.
- **The Commands page edits the player's `commands.toml` with tomlkit, so their comments stay.** A change is written only if the file then loads (`CommandMatcher.default`). Added phrases go in `[[phrases]]` entries; never copy a built-in command.

## Testing

- `tests/fakes/fake_setup_pc.py` and `fake_key_check.py` stand in for the PC and the key checks.
- The page can be driven in a browser with a `window.TYCHO_FAKE` bridge (see the app tests).
- Windows-only behaviour (the tray, the foreground, the windowless app) needs the real PC: install CI's build, and check it with the maintainer at the screen.
