# 2. Cascaded voice pipeline, measured before committing

Date: 2026-10-29 · Status: proposed (pending the latency spike)

## Context

Realtime speech-to-speech APIs exist, but they make tool calls less deterministic, cost more per hour, and wouldn't let Claude be the brain. Push-to-talk already marks the end of speech.

## Decision

Use separate stages: speech-to-text, then the fast path or Claude, then text-to-speech. Each provider sits behind a small typed interface.

Starting candidates, to be confirmed by the latency spike with Star Citizen running:

| Stage | Primary | Fallback |
|---|---|---|
| Speech-to-text | NVIDIA Nemotron streaming, local, CPU | Deepgram Nova-3 with keyterms |
| Sorting | Claude Haiku 5.5 | |
| Reasoning, vision | Claude Sonnet 5.5, streamed, prompt-cached | Claude Haiku 5.5 |
| Text-to-speech | Cartesia Sonic | ElevenLabs Flash |
| Short acknowledgements | Pre-rendered audio | Kokoro, local |

## Consequences

- The latency spike must measure key release to final transcript, the frame-rate cost of local models, fast-path time, Claude time to first token (cache warm and cold), time to first audio, and cost per hour.
- Licences for Nemotron and Silero VAD must be checked before shipping them.
