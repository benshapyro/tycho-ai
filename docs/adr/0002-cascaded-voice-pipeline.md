# 2. Cascaded voice pipeline, measured before committing

Date: 2026-10-29 · Status: proposed (pending the latency spike)

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

## Consequences

- The latency spike must measure key release to final transcript, the frame-rate cost of local models, fast-path time, Claude time to first token (cache warm and cold), time to first audio, and cost per hour.
- Licences for Nemotron and Silero VAD must be checked before shipping them.
