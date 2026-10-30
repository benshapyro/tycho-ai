# 7. The name: Tycho

Date: 2026-10-30 · Status: accepted

## Context

The project started as "Ultimo". Said aloud as an assistant's name ("hey Ultimo"), it didn't flow the way Siri, Alexa or Jarvis do. The name is also the default wake word, so it has to work in speech, not just on a page.

## Decision

The project is called **Tycho**, after the astronomer Tycho Brahe, who charted the stars with unmatched precision before the telescope existed: a navigator's name for a co-pilot.

- Distribution and repository: `tycho-ai`. Import package: `tycho`.
- Not "tycho-copilot": "Copilot" is a heavily used Microsoft trademark.

**Rules any future name must also meet:**
- **Not a name from the game.** A wake word that is also a place, ship or character breaks speech recognition and commands ("Orison, set a route to Orison"). Tycho has no matches on the Star Citizen wiki (checked 2026-10-30).
- Not a common English word, so it doesn't trigger by accident.
- Two or three syllables, easy to say in any accent.
- Works beyond Star Citizen, since the project may grow past one game.

## Consequences

- People say both "TIE-ko" and "TEE-ko". The wake word and the fast path should accept the common transcriptions of both, measured with real recordings.
- The bare `tycho` name on PyPI belongs to an unrelated, inactive project, hence `tycho-ai`.
- A trademark search for software and AI products is still needed before any public launch.
