# The brain (ADR 14)

Claude decides what to do with anything the fast path doesn't match. Its output is untrusted: it becomes commands that the safety gate checks. Read the root AGENTS.md first.

## Files

- `rounds.py`: what every adapter does with a complete tool call (`Round`): start a look-up, refuse an unbound act, or take the first act. The rules below live here once.
- `claude.py`: the adapter. One request is up to `MAX_CALLS` calls to Claude: look-ups run, their results go back, Claude carries on.
- `openrouter.py`: the same through OpenRouter, for any model (#192). Players get it with `brain_provider = "openrouter"` (#112), on a model from `providers`; the eval uses it on any model. Web search runs through the player's OpenRouter key (`knowledge/web_plugin.py`, #365), as it does for Claude direct when the player has stored one.
- `openrouter_http.py`: OpenRouter over HTTP: the `ChatClient` boundary (a fake in `tests/fakes/fake_chat.py`), the real client and reading its stream and errors.
- `priming.py`: getting ready as push-to-talk goes down (#21): each adapter's `prime` opens the connection with a free request when it's been idle, or writes the cache again after a break near the cache's hour. Each adapter's `decide` calls `Primer.going` first (it waits briefly for a cache write a press or a warm-up started, #540), passes `Primer.used` to `Turn.of` so the cache counts as read once an answer starts streaming, even if it's then cut off, and calls `used` again once it has its answer; a request that fails before any answer reads no cache. Each wraps its `warm` in `Primer.warming` so a press then writes nothing twice (#533).
- `providers.py`: the allowlist players pick from (#112). A model goes in only after the eval scores it with zero wrong presses; the settings schema refuses anything else. The wiring is in `src/tycho/brain_choice.py`.
- `decisions.py`: what Claude can decide (`RunActions`, `RunSequence`, `Reply`), `parse_tool_call`, and the errors.
- `briefing.py`: the cached system prompt. It lists the player's bound actions. With key actions off, `advisor.md` replaces the core's `tools`, `no-key`, `settings`, `tile`, `unclear`, `squad`, `safety`, `date` and `voice` sections, in words that name no game (#349), and a pack's own `{{keys:with keys|without}}` markers take their second text (#325): no line may tell the brain to press when it can't. The keys-off briefing ends with the player's own keys (`command_keys`, `action_keys`, #392), where Tycho reads the game's keybinds, so the brain names the key for any wording; a phrase the fast path matched also gets its keys in a bracketed line with the request (`commands/keys.py`, #386). Neither may be read aloud: the eval fails a reply that quotes them.
- `lookups.py`: the `Lookup` Protocol and `run_lookup`, which never raises.
- `heard.py`: what the player said for the request being decided, set by `rounds.traced` around it, for a look-up that must check the player's own words (the memory tool, ADR 8: that a note holds only what they said, `from_said`, or that they asked for it). Read it with `heard()`; a fake brain or test sets it with `hearing`.
- `player_keys.py`: the player's own keys as a look-up (#357), offered while key actions are on (with them off the briefing lists every key): an action's name or a few words of what it does, to the game's words and the key. Read-only. Each adapter adds it with `keyed`, reading the keybinds it was last briefed with.
- `traces.py`: one JSON line per request for Claude sessions to read (#282; root AGENTS.md, "Brain traces"). `rounds` records the calls, tool calls and look-ups on `Turn.trace`, and `traced` writes the line; an adapter only reports its stream (`Round.streaming`, `Round.used`). A new adapter passes `traces` through and does the same.

## Rules

- **Never trust a pick.** Every action is checked against the bound set in `parse_tool_call`, and then by the safety gate. An unbound one is answered ("did you mean ...") and never pressed.
- **Only the first act in a reply is pressed.** The stream is left there, so an act is as fast as possible. A fleet or settings note in the same reply (`instant`) waits up to `INSTANT_SECONDS`.
- **Nothing presses after a web search.** Its results can't be checked, so `NO_PRESS_AFTER_SEARCH`.
- **Nothing presses after a search of past conversations either** (ADR 14's history amendment, #484): a look-up with `recalls` sets `Turn.recalled`, and `unpressed` turns any act after it into `NO_PRESS_AFTER_RECALL`. Its reply goes into the follow-ups only as `RECALLED`, never its words, so "do that again" has nothing old to press.
- **Only the acts are strict** (`claude.STRICT`: `run_actions`, `run_sequence`, `show_card`). Anthropic compiles strict tools into one grammar and refuses every request once it's too large; 18 strict tools did that and the brain didn't start. A look-up checks its own arguments. `tests/test_tool_budget.py` holds the strict schemas to a size budget: don't make a look-up strict or grow an act's schema past it.
- **An empty act is no answer** (#666). `run_actions` with no actions is a reply only with a real `say`; a placeholder ("unused", "n/a", empty: `decisions.PLACEHOLDERS`) is never spoken, in any act. The model is told (`EMPTY_ACT`) and answers next call, unless its reply's words already did (`Round.said`; a lead-in such as "Let me check." never counts, #669). "None." and "Nothing." can be answers, so they aren't placeholders. A good card with a placeholder line still shows, said as `CARD_LINE` (#669).
- **Keep the cache.** Every call sends the same tools and system prompt; the briefing is rebuilt only when the keybinds change. Changing the tools, `TOOL_CHOICE` or the briefing costs a cache write.
- **A look-up may set** `seconds`, `from_the_web`, `instant` and `credit`, read with `getattr`: spell them exactly.
- **A miss is an answer, not an error.** `is_error` makes Claude retry, and each retry costs about 1-1.5 s. Return `not_found` with the closest names instead.
- **Don't take tools away on the last call.** Claude then wrote `show_card({...})` as text, and it was read aloud. `LAST_CALL` text tells it to answer now instead.

- **The briefing's wording follows `docs/voice-and-tone.md`.** `core.md` and `advisor.md` hold the rules every game shares, so their examples name no game (`advisor.md` today; `core.md` is #421): a game's words go in its pack's `briefing.md`. The eval grades the wording too (`never_say`, `say_words`, `max_words`).

## Testing

- `tests/test_openrouter.py` scripts an OpenAI-style stream (`tests/fakes/fake_chat.py`).
- `tests/test_brain.py` scripts Claude's stream (`_Calls`, `_looks_up`, `_picks`, `_reply`). It needs no key.
- Any change here should be judged by the eval, which is paid (about 5 cents a run, ask first): `uv run python tools/bench/tycho_eval.py`. The bar is zero wrong presses. Compare the mean calls a request and the acting p50 with the last run.
