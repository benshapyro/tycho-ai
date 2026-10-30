# Ultimo

**An unofficial voice co-pilot for Star Citizen®.**

> *Ultimo* (spacer slang): someone reliable and trustworthy.

Ultimo turns natural speech into in-game actions, using your own keybinds. It checks the game's state before flipping a switch, says what it did, and asks before doing anything you can't undo. It answers trade, loadout and location questions from live community data.

**Status: pre-alpha.** Nothing works yet. We're measuring latency before committing to an architecture.

## Principles

- **Never presses blind.** It tracks what it knows about your ship, where each fact came from, and how sure it is. It checks before toggling.
- **Receipts, not claims.** Every action is logged and reported: what was pressed, and whether it was confirmed.
- **Fast where it matters.** Known commands skip the AI entirely. Target: under 300 ms from the end of speech to the keypress.
- **Your screen is yours.** Vision is off unless you ask, captures only the game window, and keeps nothing by default.
- **Plays by the rules.** It sends keypresses through your keybinds only. It never reads game memory or injects into the game. Every action starts from you; there's no unattended automation.
- **Measured, not claimed.** Accuracy and latency are benchmarked against a recorded test set.

## Requirements

Windows 10 or 11, and Star Citizen. Development uses [uv](https://docs.astral.sh/uv/).

## Development

```sh
uv sync
uv run pytest
uv run ruff check
uv run pyright
```

See [docs/architecture.md](docs/architecture.md) for the design, [docs/adr](docs/adr) for the decisions behind it, and [CONTRIBUTING.md](CONTRIBUTING.md) to get involved.

## Data sources

Ultimo uses community data from the [Star Citizen Wiki API](https://api.star-citizen.wiki) and [UEX Corp](https://uexcorp.space). Thank you to both. Each user supplies their own UEX API token.

## Licence

[Apache-2.0](LICENSE).

---

This is an unofficial Star Citizen fan project, not affiliated with the Cloud Imperium group of companies. All content in this project not authored by its host or users is property of its respective owners. Star Citizen®, Roberts Space Industries® and Cloud Imperium® are registered trademarks of Cloud Imperium Rights LLC. Visit the official site at [robertsspaceindustries.com](https://robertsspaceindustries.com).
