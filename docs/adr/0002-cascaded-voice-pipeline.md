# 2. Cascaded voice pipeline, measured before committing

Date: 2026-10-29 · Status: accepted 2026-10-30, after the latency spike ([#1](https://github.com/benshapyro/tycho-ai/issues/1)). Provider choices are superseded by [ADR 6](0006-cloud-brain-local-ears.md) (the brain) and [ADR 9](0009-voice.md) (the voice); the tables below are the original plan, kept as history.

## Context

Realtime speech-to-speech APIs exist, but they make tool calls less deterministic, cost more per hour, and wouldn't let Claude be the brain. Push-to-talk already marks the end of speech.

## Decision

Use separate stages: speech-to-text, then the fast path or Claude, then text-to-speech. Each provider sits behind a small typed interface.

Public benchmarks such as the Open ASR Leaderboard score long-form read speech. Tycho hears two-second commands, full of jargon, over game noise. So speech-to-text is chosen by recording the user's phrases once and running every candidate against them.

Speech-to-text candidates for the spike:

| Group | Candidates |
|---|---|
| Local, fast, CPU | NVIDIA Nemotron Speech Streaming (vocabulary boosting); Parakeet-TDT |
| Local, accuracy leaders (measure FPS cost) | Qwen3-ASR-1.7B; Nemotron 3.5 Transcribe |
| Local baseline | Whisper large-v3-turbo, with a vocabulary prompt |
| Cloud | Deepgram Nova-3 and AssemblyAI Universal-Streaming, both with keyterms; Soniox or Speechmatics |

Other stages start from these:

| Stage | Primary | Fallback |
|---|---|---|
| Sorting | Claude Haiku 5.5 | |
| Reasoning, vision | Claude Sonnet 5.5, streamed, prompt-cached | Claude Haiku 5.5 |
| Text-to-speech | Cartesia Sonic | ElevenLabs Flash |
| Short acknowledgements | Pre-rendered audio | Kokoro, local |

## Outcome (2026-10-30)

Measured on the author's RTX 4080 Laptop, with Star Citizen running, on 30 recorded phrases and an 82-case typed eval. Details are in [#1](https://github.com/benshapyro/tycho-ai/issues/1).

| Stage | Result |
|---|---|
| Speech-to-text | **Parakeet TDT 0.6B v2**, local: about 0.1 s on GPU, 0.6 s on CPU. With the game-term lexicon corrector, 29 of 30 phrases exact. Whisper large-v3-turbo and Qwen3-ASR were as accurate but about 2 s. Nemotron streaming wasn't tested; deprioritised because Parakeet is already fast. |
| Fast path | Local command matcher plus keybinds read from the game: about 6 ms, no wrong actions on the 30 phrases. |
| Router | TypeSafe Jev: about 0.12 s, good at routing questions and unclear requests, not trusted to pick actions yet. See [ADR 10](0010-interaction-modes-and-routing.md). |
| Brain | **Claude Haiku 5.5** at effort low: 97% on the 82-case eval, 1.2 s typical, 2.1 s at p90. See [ADR 6](0006-cloud-brain-local-ears.md). |
| Text-to-speech | **ElevenLabs**, with Kokoro as the local fallback. See [ADR 9](0009-voice.md). |

Not yet measured: the frame-rate cost of local models, and a held-out recording set with more voices.

## Consequences

- The latency spike must measure key release to final transcript, the frame-rate cost of local models, fast-path time, Claude time to first token (cache warm and cold), time to first audio, and cost per hour.
- Licences for Nemotron and Silero VAD must be checked before shipping them.
