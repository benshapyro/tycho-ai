# 10. Interaction modes and routing

Date: 2026-10-30 · Status: accepted

## Context

Players don't only give commands. They ask questions, chat, plan trade runs, change settings, and talk to squadmates with push-to-talk still held. Comparable products (Wingman AI, Xbox Gaming Copilot, NVIDIA G-Assist, car assistants, Alexa) recognise several kinds of request. Switching modes explicitly ("chat mode") adds friction.

Measurements from the latency spike ([#1](https://github.com/benshapyro/tycho-ai/issues/1)) shape the routing:
- The local fast path handles known commands in about 0.1 s.
- Claude Haiku 5.5 decides everything else at 97% on the 82-case eval, in about 1.2 s.
- TypeSafe's Jev answers routing questions in about 0.12 s. At a 0.9 confidence threshold it routed questions and unclear requests well, but made 3 wrong single-action picks, and its "does the player really mean eject?" checks were unreliable (for example 0.84 for "eject eject eject", 0.43 for "get me out of here").
- Cerebras-hosted models engaged quantum travel and fired missiles without being asked.

## Decision

**Every utterance is routed to a mode, without the player switching modes:**

| Mode | Example | Handling |
|---|---|---|
| Command | "Gear down" | Fast path, otherwise the brain |
| Quick lookup | "Nearest refinery?" | One answer from live data |
| Chat | "Tell me about the Vanduul" | Conversational, in persona |
| Planning | "Help me plan a trade run" | Multi-turn, ending in confirmed steps |
| Coach | "How does mining work?" | Explains, can walk through steps |
| Settings | "Talk less" | Changes Tycho itself ([ADR 5](0005-settings-one-schema-three-ways-in.md)) |
| Combat | automatic, or "combat mode" | Replies shrink to acknowledgements; no chat |
| Callouts | "Fuel at 10%" | Tycho speaks first, rate-limited |
| Not for Tycho | talking to a squadmate | Ignored silently |
| Dictation (optional) | "Type in chat: o7" | Only when asked explicitly |

**Routing order:** exact fast-path match, then a fast router, then the brain.

**Routing rules:**
- **When unsure, talk, never act.** Commands need high confidence; anything uncertain is treated as a question or handed to the brain. A wrong answer is cheap; a wrong eject is not.
- **Follow-ups stick.** For about 20 to 30 seconds after a reply, a short follow-up ("and the second one?") stays in the current mode.
- **A clear order always wins.** An imperative ("gear down") goes to command mode, even mid-conversation.
- **Pressing push-to-talk interrupts Tycho's speech immediately.** It never interrupts a key sequence already running, unless the player says "stop" or "cancel".
- **Misroutes are easy to correct and are logged.** "No, that was a command" re-runs the last request; every misroute is added to the eval.
- **The current mode is audible**, for example a different tone for chat and combat.

**Jev's role:** a candidate fast router only. It may route questions and unclear requests, and pick single actions once a re-test with richer action descriptions shows zero wrong picks above the threshold. It never triggers a dangerous action.

**The safety gate is outside every model.** Whatever the router or brain decides, confirm-grade actions (see architecture section 4.7) need the player's spoken confirmation. No model output can skip it.

**Chat mode starts on the same pipeline** (speech-to-text, the brain, the configured voice). A realtime voice-to-voice model may come later as an optional chat provider. It would get no game-action tools, only a way to hand a request back to the command path.

## Consequences

- The eval grows a labelled routing set, including "not for Tycho" speech.
- Combat mode needs a signal from game state (the log or vision) or a voice toggle.
- Planning mode needs a short-term scratchpad and a way to turn a plan into confirmed actions.
