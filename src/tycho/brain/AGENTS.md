# The brain (ADR 14)

Claude decides what to do with anything the fast path doesn't match. Its output is untrusted: it becomes commands that the safety gate checks. Read the root AGENTS.md first.

## Files

- `rounds.py`: what every adapter does with a complete tool call (`Round`): start a look-up, refuse an unbound act, or take the first act. The rules below live here once.
- `claude.py`: the adapter. One request is up to `MAX_CALLS` calls to Claude: look-ups run, their results go back, Claude carries on.
- `openrouter.py`: the same through OpenRouter, for any model (#192). Players get it with `brain_provider = "openrouter"` (#112), on a model from `providers`; the eval uses it on any model. It has no web search of its own: with the player's Anthropic key it gets the usual `WebSearch`, and without one `SearchNeedsAnthropic`, which tells the model to say so.
- `providers.py`: the allowlist players pick from (#112). A model goes in only after the eval scores it with zero wrong presses; the settings schema refuses anything else. The wiring is in `src/tycho/brain_choice.py`.
- `decisions.py`: what Claude can decide (`RunActions`, `RunSequence`, `Reply`), `parse_tool_call`, and the errors.
- `briefing.py`: the cached system prompt. It lists the player's bound actions. With key actions off, `advisor.md` replaces the core's `tools`, `no-key`, `settings`, `tile`, `unclear`, `squad`, `safety`, `date` and `voice` sections, in words that name no game (#349), and a pack's own `{{keys:with keys|without}}` markers take their second text (#325): no line may tell the brain to press when it can't.
- `lookups.py`: the `Lookup` Protocol and `run_lookup`, which never raises.
- `traces.py`: one JSON line per request for Claude sessions to read (#282; root AGENTS.md, "Brain traces"). `rounds` records the calls, tool calls and look-ups on `Turn.trace`, and `traced` writes the line; an adapter only reports its stream (`Round.streaming`, `Round.used`). A new adapter passes `traces` through and does the same.

## Rules

- **Never trust a pick.** Every action is checked against the bound set in `parse_tool_call`, and then by the safety gate. An unbound one is answered ("did you mean ...") and never pressed.
- **Only the first act in a reply is pressed.** The stream is left there, so an act is as fast as possible. A fleet or settings note in the same reply (`instant`) waits up to `INSTANT_SECONDS`.
- **Nothing presses after a web search.** Its results can't be checked, so `NO_PRESS_AFTER_SEARCH`.
- **Keep the cache.** Every call sends the same tools and system prompt; the briefing is rebuilt only when the keybinds change. Changing the tools, `TOOL_CHOICE` or the briefing costs a cache write.
- **A look-up may set** `seconds`, `from_the_web`, `instant` and `credit`, read with `getattr`: spell them exactly.
- **A miss is an answer, not an error.** `is_error` makes Claude retry, and each retry costs about 1-1.5 s. Return `not_found` with the closest names instead.
- **Don't take tools away on the last call.** Claude then wrote `show_card({...})` as text, and it was read aloud. `LAST_CALL` text tells it to answer now instead.

## Testing

- `tests/test_openrouter.py` scripts an OpenAI-style stream (`tests/fakes/fake_chat.py`).
- `tests/test_brain.py` scripts Claude's stream (`_Calls`, `_looks_up`, `_picks`, `_reply`). It needs no key.
- Any change here should be judged by the eval, which is paid (about 10 cents a run, ask first): `uv run python tools/bench/tycho_eval.py`. The bar is zero wrong presses. Compare the mean calls a request and the acting p50 with the last run.
