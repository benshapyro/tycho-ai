# 1. Build from scratch, in Python, on asyncio

Date: 2026-10-29 · Status: accepted

## Context

Wingman AI (GPL-3.0) already offers a Star Citizen voice assistant. An audit found it lacks Tycho's core features: checking state before toggles, confirmation grades, a stop that interrupts sequences, and window-only vision. These sit in its core command path, where its plugin system can't reach. It also stores API keys in plaintext and serves them over an unauthenticated local endpoint.

## Decision

- Build Tycho from scratch, under Apache-2.0, as a clean room: no Wingman code is copied or ported.
- Use Python 3.14, managed with uv. Its audio, Windows input and AI libraries are the most mature, and network and speech-to-text time dominate the latency budget, not the interpreter.
- Build our own asyncio pipeline, not Pipecat or LiveKit Agents. Those target telephony and WebRTC. Tycho's core is push-to-talk, a fast path with no model, and cancellable key injection.
- Write our own small ctypes `SendInput` module using scancodes, so cancellation can always release held keys.

## Consequences

- More to build up front, but no GPL obligations and full control of the safety-critical path.
- A native (PyO3) component stays an option if profiling demands one.
