# Privacy policy

Tycho is an open-source program that runs on your PC. The Tycho project runs no servers of its own, and nobody behind Tycho receives anything about you or how you use it.

## In short

- **Your voice never leaves your PC.** Speech is turned into text on your PC.
- **Only text is sent, and only to services you gave a key for:** what you said goes to Claude (from Anthropic, or through OpenRouter if you chose it), and what Tycho says goes to ElevenLabs. The one exception is a picture of the game window, only if you turned looking on and only when you ask Tycho to look.
- **Tycho counts and tracks nothing about how you use it.** There are no analytics. Crash reports are sent only if you say yes.
- **What Tycho keeps stays on your PC:** your settings, your memory notes, your blueprints, and your past conversations if you turned history on. Your API keys are kept in Windows Credential Manager, never in a file or a log.

## Everything that leaves your PC

| Service | What it receives | When |
|---|---|---|
| Anthropic (Claude) | What you said (as text); your bound actions with their keys, your commands and sequences; and your last few requests with what Tycho did, if they were in the last 30 seconds | Requests the built-in commands don't handle, plus one warm-up request when Tycho starts |
| Anthropic or OpenRouter (whichever runs the brain) | Your memory notes: short notes about you, for every game and for the game you're playing. Facts you mention about how you play are kept as you say them; anything else only when you ask Tycho to remember it. They're kept on your PC in `memory.toml` files you can read and edit, and on the Memory page | With each request to the brain, unless memory is off (`memory = false`, or say "turn memory off"). Never in crash reports |
| Anthropic or OpenRouter (whichever runs the brain) | In Star Citizen, the blueprints you own: item names only, kept from the game's log (each blueprint a mission gives you, and once from your older logs) and from what you tell Tycho. They're kept on your PC in `blueprints.toml`, which you can read and edit, and on the Memory page | Only inside the answer when you ask about blueprints, unless memory is off. Never in crash reports |
| Anthropic or OpenRouter (whichever runs the brain) | Parts of your past conversations: a few short matching lines of what you and Tycho said, kept on your PC for 30 days | Only with history on (`keep_history`, asked once in setup; off unless you say yes), and only when you ask about earlier talk ("what was yesterday's route?"). Never in crash reports |
| OpenRouter, instead of Anthropic | The same as Anthropic (Claude) above. OpenRouter forwards it to Anthropic, who runs the model, so both receive it | Only with `brain_provider = "openrouter"`, instead of sending it to Anthropic directly |
| ElevenLabs | The text Tycho speaks; at start, which voice you chose, to check your key can use it | When Tycho speaks, and once when it starts |
| The key's own provider (Anthropic, OpenRouter or ElevenLabs) | A key you store, to check it works | When you store it, and when you run `tycho check` |
| Star Citizen Wiki API, UEX Corp | Nothing about you: Tycho downloads their ship lists | On first run, and when a new patch goes live |
| OpenRouter and Perplexity (web search, with an OpenRouter key) | A short search query Claude writes from your question. OpenRouter passes it to Perplexity's search and the results to Claude on Anthropic, and asks both to keep nothing | Questions about news, upcoming patches, events and new ships, and where something is or how to do something when Tycho's look-ups don't cover it; off with `web_search = false`. Any website; `web_search_open = false` keeps it to the Star Citizen sites. Whenever you've stored an OpenRouter key, whichever runs the brain |
| Anthropic (web search, with only an Anthropic key) | The same search query | The same questions. The Star Citizen sites first; when they have no clear answer, any website, and Anthropic reads up to two of the pages it finds (never Reddit's pages, only its search snippets); `web_search_open = false` keeps it to the Star Citizen sites |
| UEX Corp | Which commodity or trading place you asked about | Trade questions |
| Star Citizen Wiki API | The item, blueprint or mission giver names you ask about | Weapon, armor, component, crafting and blueprint questions |
| GitHub | Nothing about you: Tycho asks whether a newer version is out | When Tycho starts and when its window opens, off with `check_for_updates = false`; and when you press **Check for updates** |
| Hugging Face | Nothing about you: Tycho downloads its speech model | Once, the first time Tycho starts |
| Sentry (crash reports) | Only if you said yes: the kind of error and where in Tycho's own code it happened, Tycho's version, the Windows version, the game and the brain provider. Never what you or Tycho said, your keys, your name, your files or folders, or anything that identifies you or your PC | The next time Tycho starts after a crash. Saying no deletes any report still waiting |
| Anthropic (Claude), or OpenRouter instead | A picture of the game's own window (never the desktop or another window), with your question. Never saved or logged | Only if you said yes to looking (`look_at_screen`, off until you do), and only when you ask Tycho to look ("take a look") or say yes when it offers. You hear a camera click each time |

## Turning each one off

- **The brain (Anthropic or OpenRouter):** don't store a key. The built-in commands still work, with nothing sent.
- **The voice (ElevenLabs):** don't store a key, or set `voice_volume = 0`. Tycho's replies are then shown as text instead of spoken.
- **Memory notes:** `memory = false` in your settings, or say "turn memory off".
- **History:** off unless you said yes in setup (`keep_history`).
- **Looking at the game window:** off unless you said yes (`look_at_screen`).
- **Web search:** `web_search = false`, or `web_search_open = false` to keep it to the Star Citizen sites.
- **The update check (GitHub):** `check_for_updates = false`.
- **Crash reports (Sentry):** off unless you said yes (`crash_reports`); saying no later deletes any report still waiting.

Your settings file is `%APPDATA%\tycho\settings.toml`. Most of these are also in Tycho's window, in Settings under Privacy, and setup asks about crash reports, looking and history before Tycho first listens.

## The services' own privacy policies

What a service does with what it receives is set by that service:
[Anthropic](https://www.anthropic.com/legal/privacy),
[OpenRouter](https://openrouter.ai/privacy),
[ElevenLabs](https://elevenlabs.io/privacy-policy),
[Perplexity](https://www.perplexity.ai/hub/legal/privacy-notice),
[Sentry](https://sentry.io/privacy/),
[GitHub](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement) and
[Hugging Face](https://huggingface.co/privacy).
The [Star Citizen Wiki API](https://api.star-citizen.wiki) and [UEX Corp](https://uexcorp.space) receive no information about you beyond what any website receives: the names you ask about, and your IP address.

## When this changes

A change to what leaves your PC is a change to this page, made in a pull request anyone can read, and listed in the [changelog](CHANGELOG.md). The installer shows a summary of this page before Tycho first starts.
