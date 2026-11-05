# Tile window test

Before the Tile is built (#2), this checks that a see-through card can sit over Star Citizen without getting in the way. It tests the window, not the cards.

```powershell
uv run python tools/spike/tile/tile_window.py                 # tkinter engine, about 2 minutes
uv run python tools/spike/tile/tile_window.py --engine qt     # Qt WebEngine: the real look
uv run python tools/spike/tile/tile_window.py --seconds 600   # a longer play session
```

Start it, switch to the game and play as usual. A sample card cycles in the top-right corner (`--corner` moves it). At the end it asks two questions only you can answer, prints the results, and saves them to `%LOCALAPPDATA%\tycho\logs\tile-test-<engine>-<time>.json`.

| Check | How it's measured | Needs the game |
|---|---|---|
| Never takes focus | the foreground window is never the card's, sampled every 0.5 s | no |
| Clicks pass through | what a click at the card's centre would reach | no |
| Stays on top | it keeps the always-on-top style, and lists any windows above it | in game, to be sure |
| You saw it | asked at the end | yes, for "over the game" |
| No anti-cheat trouble | asked at the end; it also notes if the game closed during the test | yes |
| Graphics memory | the whole card's use less what it was before, via nvidia-smi | best with the game |

The engines run in their own process, and the test only watches from outside. Nothing here touches the game: no injection, no reading its memory, no keys sent.

## Engines

- **tk:** tkinter, already in Python. It can make only one exact colour see-through, so edges are hard and the background solid. That's enough for the window checks.
- **qt:** PySide6 with Qt WebEngine. It draws the real card HTML (`card.html`), truly see-through. It runs in its own environment (PEP 723), so PySide6 is never added to Tycho. The first run downloads about 400 MB into uv's cache. Its memory figure is the one that matters if the Tile is drawn with web technology.
- **pywebview** (#12's earlier choice) isn't tried: its docs say transparent windows aren't supported on Windows.

## What we learned on the desktop (2026-11-04)

- **Starting tkinter takes the foreground before any window is shown.** Windows lets a program started from the foreground window (here, the terminal) take the foreground once, and tkinter's hidden main window does. Setting the window's styles can't prevent it, so the engine records which window was in front before creating any UI and gives it back afterwards. The real Tile needs the same.
- **Screenshots can miss the Tile.** A plain screen copy leaves out layered windows unless it asks for them (`CAPTUREBLT`). Some capture or streaming tools may not show the Tile.

## Next play session

1. `uv run python tools/spike/tile/tile_window.py --seconds 300`, then play. Optionally run it again with `--engine qt`.
2. `uv run python tools/bench/gpu_memory.py recordings/2026-10-30_070326 --with-game`.
3. One screenshot in each ship you fly, to confirm HUD colours (`docs/research/manufacturer-ui.md`). Keep the screenshots on your PC; only their hex values go in the doc.
