# Tycho

**An unofficial voice co-pilot for Star Citizen®.**

> Named after Tycho Brahe, who charted the stars with unmatched precision before the telescope existed.

Tycho turns natural speech into in-game actions, using your own keybinds. It checks the game's state before flipping a switch, says what it did, and asks before doing anything you can't undo. It answers trade, loadout and location questions from live community data.

**Status: pre-alpha, working towards 0.1.** The talking loop runs end to end: hold push-to-talk, say a common command such as "gear down and request landing", and Tycho presses your keys. It works on the fast path only for now. Actions you can't undo are refused until spoken confirmation arrives in 0.2, Claude handles everything else from 0.3, and spoken replies arrive in 0.4 ([ADR 11](docs/adr/0011-runtime-architecture.md)).

## Trying it

```sh
uv sync
uv run tycho check   # finds the game, reads your keybinds, tests the microphone
uv run tycho bind    # choose the push-to-talk button (a mouse side button works well)
uv run tycho run     # downloads the speech model the first time (2.5 GB)
```

Tycho only presses keys while Star Citizen is the window in front. You hear a click when a command was done, and a low tone when it wasn't; `uv run tycho sounds` plays both. Settings live in `%APPDATA%\tycho\settings.toml`, and logs in `%LOCALAPPDATA%\tycho\logs`. Set `TYCHO_MODELS` to keep the speech model on another drive.

## Principles

- **Never presses blind.** It tracks what it knows about your ship, where each fact came from, and how sure it is. It checks before toggling.
- **Receipts, not claims.** Every action is logged and reported: what was pressed, and whether it was confirmed.
- **Fast where it matters.** Known commands skip the AI entirely. Target: under 300 ms from the end of speech to the keypress.
- **Your screen is yours.** Vision is off unless you ask, captures only the game window, and keeps nothing by default.
- **Plays by the rules.** It sends keypresses through your keybinds only. It never reads game memory or injects into the game. Every action starts from you; there's no unattended automation.
- **Measured, not claimed.** Accuracy and latency are benchmarked against a recorded test set.

## Requirements

- Windows 10 or 11, and Star Citizen.
- An NVIDIA GPU makes speech recognition fastest; it also runs on the CPU.
- An Anthropic API key (for Claude), and an ElevenLabs API key for the recommended voice. A free local voice is the fallback. Store a key with `uv run python -m tycho.credentials set anthropic`; without one, Tycho understands only its built-in phrasings. For the voice, `uv run python -m tycho.credentials set elevenlabs` (the key needs text-to-speech permission); without one, Tycho's replies go to the log and it plays its sounds.
- Development uses [uv](https://docs.astral.sh/uv/).

## What leaves your PC

Your voice never does: speech recognition runs on your PC. Only text is sent, and only to services you set up with your own keys:

| Service | What it receives | When |
|---|---|---|
| Anthropic (Claude) | What you said (as text), your list of bound actions, commands and sequences, and your last few requests if they were in the last 30 seconds | Requests the fast path doesn't handle, plus one warm-up request when Tycho starts |
| ElevenLabs | The text of Tycho's reply | When Tycho speaks with the ElevenLabs voice |
| TypeSafe (Jev) | What you said (as text) | Only if you turn on the fast router |
| UEX Corp, Star Citizen Wiki | Search terms, such as a commodity or place name | When you ask a trade, item or location question |
| Anthropic (Claude) | A capture of the game window | Only when vision is on and you ask Tycho to look |

## Development

```sh
uv sync
uv run pytest
uv run ruff check
uv run pyright
```

See [docs/architecture.md](docs/architecture.md) for the design, [docs/adr](docs/adr) for the decisions behind it, and [CONTRIBUTING.md](CONTRIBUTING.md) to get involved.

## Data sources

Tycho uses community data from the [Star Citizen Wiki API](https://api.star-citizen.wiki), [starcitizen.tools](https://starcitizen.tools) and [UEX Corp](https://uexcorp.space). Thank you to all three. Each user supplies their own UEX API token.

## Licence

[Apache-2.0](LICENSE).

---

This is an unofficial Star Citizen fan project, not affiliated with the Cloud Imperium group of companies. All content in this project not authored by its host or users is property of its respective owners. Star Citizen®, Roberts Space Industries® and Cloud Imperium® are registered trademarks of Cloud Imperium Rights LLC. Visit the official site at [robertsspaceindustries.com](https://robertsspaceindustries.com).
