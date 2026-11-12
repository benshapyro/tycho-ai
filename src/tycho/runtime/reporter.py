"""Reporting: how one request ended, in the log, the console and the action log (#55).

The session decides what is said and played; this writes it down. No threads.
"""

import logging

from tycho import logs
from tycho.runtime.conversation import would_speak
from tycho.runtime.messages import Result
from tycho.runtime.ports import ActionLog

log = logging.getLogger("tycho.runtime.session")
"""The session's logger, so the log reads as it always has."""


class Reporter:
    def __init__(self, *, log_transcripts: bool = False, action_log: ActionLog | None = None):
        self._log_transcripts = log_transcripts
        self._action_log = action_log

    def report(self, result: Result, *, voice: bool, speak: bool) -> None:
        """Log ``result``, and record it in the action log if it matched a command.
        ``voice``: Tycho has a voice. ``speak``: it will say ``result.say``."""
        self._log(result, voice=voice, speak=speak)
        if result.commands and self._action_log is not None:
            self._record(self._action_log, result, spoken=speak)

    def _log(self, result: Result, *, voice: bool, speak: bool) -> None:
        stages = ", ".join(f"{name} {ms:.0f}" for name, ms in result.timings.items())
        said = ""
        if self._log_transcripts and result.text:
            said = f' "{result.text}"'
            if result.heard and result.heard != result.text:  # the lexicon corrected it
                said += f' (heard "{result.heard}")'
        keydown = "" if result.keydown_ms is None else f"key down at {result.keydown_ms:.0f}, "
        log.info(
            "#%d%s: %s%s%s%s in %.0f ms (%s%s)",
            result.request,
            said,
            "+".join(result.commands) + " " if result.commands else "",
            "confirmed, " if result.confirmed else "",
            result.status,
            f" ({result.detail})" if result.detail else "",
            result.total_ms,
            keydown,
            stages or "no stages",
        )
        # What Tycho says often echoes what the player said, so it's kept like a transcript.
        if result.say and self._log_transcripts:
            quiet = "" if speak else f" (not spoken: {_quiet(result, voice=voice)})"
            log.info("Tycho%s: %s", quiet, result.say)
        elif not voice:
            # No voice: the console is the only place the player can read the answer
            # (audit, 2026-11-03). Not the log file, unless they keep transcripts.
            if result.say and would_speak(result):
                log.info("Tycho: %s", result.say, extra=logs.CONSOLE_ONLY)
            elif result.status == "asking" and result.question:
                log.info("Tycho asks: %s", result.question, extra=logs.CONSOLE_ONLY)

    def _record(self, action_log: ActionLog, result: Result, *, spoken: bool) -> None:
        entry: dict[str, object] = {
            "request": result.request,
            "commands": list(result.commands),
            "status": result.status,
            "pressed": list(result.pressed),
            "confirmed": result.confirmed,
        }
        if result.keydown_ms is not None:
            entry["keydown_ms"] = round(result.keydown_ms)
        if result.detail:
            entry["detail"] = result.detail
        if result.by_brain:
            entry["by"] = "brain"
        if result.say and self._log_transcripts:
            entry["tycho_said"] = result.say
            entry["spoken"] = spoken
        if self._log_transcripts and result.text:
            entry["said"] = result.text
            if result.heard and result.heard != result.text:
                entry["heard"] = result.heard
        try:
            action_log.record(entry)
        except OSError as e:  # a full disk mustn't stop the loop
            log.warning("Couldn't write the action log: %s", e)


def _quiet(result: Result, *, voice: bool) -> str:
    """Why a line wasn't spoken, for the log."""
    if not voice:
        return "no voice"
    if result.done and result.announced:
        return "the game announces it"
    return result.status
