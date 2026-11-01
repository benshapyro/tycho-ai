# 15. Knowledge: look-ups, sources and the player's fleet

Date: 2026-11-01 · Status: accepted

## Context

The brain answers game questions from what Claude learned in training, plus a hand-kept facts sheet (`brain/facts.md`) in the briefing. That isn't enough. The two worst answers so far were both ship facts: the Andromeda called an Aegis ship (it's an RSI Constellation), and the Caterpillar called poor for solo hauling because the sheet suggested its Command Module was bought separately. A hand-kept sheet can't cover 200+ ships, live prices, items or blueprints.

The architecture lists Knowledge as stage 3 ("trade, loadout and location questions answered with a cited source"), and ADR 8 says outside services connect through MCP. This decides how Knowledge is built, with Ben's answers to the open questions (1 November 2026).

## Decision

**Look-ups are tools inside the brain, in a loop.** Claude can ask for a look-up, Tycho runs it and returns the result, and Claude answers. This needs the groundwork in [#54](https://github.com/benshapyro/tycho-ai/issues/54) first: a provider-neutral tool registry where each tool is either an *act* (a press) or a *look-up*, one request object for the brain, and a time budget for the loop. An act still leaves the loop at once and goes through `commands_for` and the safety gate.

**Tycho's own sources are built-in tools, not MCP.** They're faster, need no extra process, can be tested with fakes, and work offline from local copies. MCP stays for connections players add themselves (ADR 8). Tycho's data could be offered as an MCP server later.

**v1 covers ships, trade prices, and items and blueprints:**

| Tool | Source | Fetched |
|---|---|---|
| Ships | Star Citizen Wiki API | A local copy, refreshed each patch |
| Trade | UEX Corp | Live, saved for a few minutes |
| Items and blueprints | Star Citizen Wiki API, starcitizen.tools | A local copy, refreshed each patch |
| Web search | Anthropic's web search tool | Last resort, only when the others miss |

How often data changes decides how it's fetched: specs change each patch, prices each hour.

**Ship data: measure two designs and keep the better one.** (a) the local copy behind a look-up tool, which adds a second Claude call; (b) a compact table of every ship in the cached briefing, which needs no loop but roughly triples the briefing. The eval decides, on accuracy, wait time and cost.

**Waiting.** If a look-up takes longer than about 0.7 s, Tycho says a short filler line ("Checking UEX…") and then answers. After about 4 s it gives up and says so honestly.

**Sources.** Tycho names the source out loud only when freshness matters: prices and web answers ("UEX says 3,240 at Area18, as of an hour ago"). Ship and item specs are just answered. The full source is always logged.

**UEX access.** Each player adds their own free UEX token, stored with `tycho.credentials` (Windows Credential Manager). Tycho is open source and never ships a key. Without a token, trade questions fall back to web search or an honest "I don't have that".

**The player's fleet.** v1 includes a hand-edited list of the ships the player owns, in the player profile (part of [#59](https://github.com/benshapyro/tycho-ai/issues/59)). It goes in the cached briefing, so questions like "can my Cutlass carry this?" need no look-up.

**Look-up results are untrusted.** Anyone can edit a wiki page. A result can only shape what Tycho says, never what it presses: presses come only from the command list, through the safety gate (ADR 10, ADR 13).

**Not in v1:** live game state from `Game.log` (current ship and location), places, and fleet import from other tools. They're recorded here as later sources.

## Order of work

Each is its own pull request, measured with the brain eval:

1. The #54 groundwork: tool loop, request object, look-up budget. The eval must show no change.
2. Ships: the local copy and look-up tool, and the briefing-table alternative; keep the better one. New eval cases for makers, crew and cargo.
3. Trade: the UEX tool and token, with the filler line.
4. The player's fleet in the profile.
5. Items and blueprints.
6. Web search as the last resort.

## Before building

- Read UEX's API terms and rate limits, and the Star Citizen Wiki API's.
- Confirm the Wiki API covers items and blueprints; if not, use starcitizen.tools.
- Text from starcitizen.tools is CC BY-SA: credit it wherever it's stored or shown.

## Consequences

- Answers that need a look-up take about 1–3 s longer than today's 1.1 s. The filler line covers the wait; local copies keep most look-ups to milliseconds.
- Local copies need refreshing each patch, which joins the facts sheet in the patch-day routine ([#51](https://github.com/benshapyro/tycho-ai/issues/51)).
- Web search costs about 1 cent a search, on top of the request.
- Trade answers need each player to make a UEX account.
