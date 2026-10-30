# 6. Cloud brain, local ears

Date: 2026-10-30 · Status: accepted, amended by [ADR 9](0009-voice.md) (the recommended voice also needs an ElevenLabs key)

## Context

Tycho needs a language model for anything the fast path can't handle, and a speech-to-text model for everything.

Small open-weight language models can run on a gaming PC, but players would have to install a model runner, download several GB per model, and keep them updated. They would also compete with Star Citizen for GPU memory: the game used 8 of 12 GB on the author's laptop. For most players that's too much to ask.

Speech-to-text is different. In the latency spike, Parakeet TDT 0.6B v2 transcribed a phrase in about 0.1 s on the GPU and 0.6 s on the CPU, and it can be downloaded and run by Tycho itself without the player managing anything.

## Decision

**The language model runs in the cloud, with the player's own API key.**
- Setup asks for a key once and stores it in Windows Credential Manager.
- Providers sit behind one interface (ADR 4). Claude is the first; OpenAI-compatible providers such as Cerebras use one shared adapter. The default is chosen by the benchmark: accuracy first, then latency, then cost.
- Audio never leaves the PC. What text goes to which provider is listed in the README ("What leaves your PC").

**Speech-to-text runs locally, and Tycho manages it.** Tycho downloads the model on first run, picks GPU or CPU, and updates it. The player never sees a model file.

**Local language models are an optional extra, not a design target.** They may come later for players who want them, behind the same interface.

## Result (2026-10-30)

The benchmark ([#1](https://github.com/benshapyro/tycho-ai/issues/1)) chose **Claude Haiku 5.5 at effort low** as the default brain:
- 97% (159 of 164 decisions) on the 82-case typed eval;
- 1.2 s typical, 2.1 s at p90;
- about $0.0002 per request with the cached briefing;
- no dangerous actions.

Compared:
- Medium effort scored the same and was slightly slower.
- Cerebras gpt-oss-120b (84%) and qwen-3.8-27b (93%) were 3 to 5 times faster, but pressed actions that must be confirmed (quantum, jump drive, missiles) without being asked.
- Claude Sonnet 5.5 and Opus 5.5 matched Haiku on the first 11 requests at higher cost and latency.

## Consequences

- The "thinking" path needs an internet connection. The fast path (common commands) keeps working offline, and Tycho says plainly when the cloud is unreachable.
- Running costs are the player's: measured at about $0.015 per hour with Claude Haiku 5.5 at one request a minute.
- The architecture goal "work well on free or local models" becomes "cheap to run, no technical setup".
- Privacy notes in the README and SECURITY.md must say what text is sent and to whom.
