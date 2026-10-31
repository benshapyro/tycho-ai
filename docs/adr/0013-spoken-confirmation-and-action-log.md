# 13. Spoken confirmation and the action log

Date: 2026-10-31 · Status: accepted

## Context

In 0.1 the safety gate refused confirm-grade actions outright (ADR 11). Release 0.2 lets the player confirm them by voice (architecture 4.7, [#19](https://github.com/benshapyro/tycho-ai/issues/19)) and keeps a log of every action.

Some constraints shape it:
- Tycho can't speak until 0.4, so it asks with a sound.
- Tycho never has an open mic. It only hears what the player says while holding push-to-talk, or after a wake word later.
- No timers act on their own (AGENTS.md).

The author decided the grades on 2026-10-31. Powering off and opening or closing the doors never ask, even in flight. Confirm-grade means eject, emergency exit, self-destruct, jettisoning cargo, firing, launching missiles and respawning.

## Decision

1. **The gate has three answers:** allow, refuse, or ask.
   - Confirm-grade actions are asked about.
   - A key shared with a dangerous action, or a missing fact, is still refused (ADR 12). Confirming can't make an unknown key safe.
2. **Asking means a question sound, then a confirmation on push-to-talk.**
   - Tycho plays a short rising two-tone. The console says what it wants to do: "Eject: hold push-to-talk and say confirm".
   - Nothing is pressed until the player confirms.
   - "Confirm", "confirmed" or "affirmative" on the next push-to-talk carries out the whole request. A chain that contains a confirm-grade part waits as a whole.
3. **The question expires after 5 seconds**, measured from when the sound plays. The `confirm_seconds` setting changes it, from 2 to 15 seconds.
   - Expiry is checked when the player next speaks; no timer runs.
   - "Confirm" after it has expired, or with nothing asked, does nothing and plays "not done".
4. **Anything else said instead cancels the question.**
   - "Cancel", "abort", "negative", "belay that" or "never mind" cancel it and play "done".
   - Any other request also drops the question, then runs as usual. A question never outlives the next thing the player says.
5. **A confirmation re-checks everything.** Tycho plans the request again with the keybinds as they are now, and runs every gate check except the question itself. If the keybinds changed in between and the press is no longer safe, it's refused.
6. **The action log** is `%LOCALAPPDATA%\tycho\logs\actions.jsonl`.
   - It has one JSON line for each request that matched a command. Each line records:
     - when it happened, and the request number;
     - the commands and their outcome (done, asked, refused, no key, game not focused, cancelled, failed);
     - the game actions actually pressed;
     - whether the player confirmed it, and why it was refused or failed.
   - What was said goes in only if the player keeps transcripts (off by default).
   - It sits behind an `ActionLog` port with a fake for tests.
   - Lines pass through the same redaction as the other logs, and the file rotates at 2 MB.
7. **One path for every source of decisions.** The fast path, and later the router and brain, all reach the hands through the same planning step, so the ask-and-confirm rule can't be skipped. Tests cover the fast path now; the brain's tests join them in 0.3.

## Consequences

- Eject is now usable by voice, at the cost of one extra push-to-talk.
- Players hear a third sound. `tycho sounds` plays all three.
- The confirm and cancel words are reserved. Commands can't use them as phrases, and a test checks this.
- Until voice arrives in 0.4, the question is only a sound plus a console line, so a player who doesn't watch the console has to learn what the rising tone means.
- The action log is the base for "undo that" and the history in the control panel.
