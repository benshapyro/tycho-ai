"""The brain's ``memory`` tool: save a note, or forget one (ADR 8, #483).

The brain never saves on its own. A note is saved only when the player's words for this
request ask Tycho to remember (``ASKED``), or are a yes to Tycho's offer in the line
just before, inside the follow-up window (``brain.heard``). Both checks read what the
player said, not what the brain claims they said. A note about pressing or confirming
is refused (``memory.unsafe``), whoever words it: the player's words are checked too.
Forgetting needs the player to have said so ("forget that", "delete the note about").
"""

import asyncio
import json
import logging
import re
from collections.abc import Mapping

from tycho.brain.heard import Heard, heard
from tycho.brain.lookups import LookupResult
from tycho.memory import MAX_NOTES, SCOPES, Memory, MemoryFileError, Note, Scope, tidy, unsafe
from tycho.text import normalize

log = logging.getLogger(__name__)

ASKED = re.compile(
    r"\b(?:remember|keep in mind|make a note|take a note|note that|don'?t forget)\b", re.I
)
"""Words that ask Tycho to keep something."""
FORGET = re.compile(
    r"\b(?:forget|delete|remove|erase|scratch|stop remembering|don'?t remember)\b", re.I
)
YES = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "absolutely", "definitely"}
    | {"correct", "right", "do", "go", "remember", "aye", "affirmative"}
)
"""The first word of a yes to the offer: "yes please", "sure, do it", "go ahead"."""
NO = frozenset({"no", "nope", "nah", "not", "never", "dont", "don"})
OFFER = re.compile(r"\bremember\b", re.I)
"""Tycho's offer names what it does: "Want me to remember you fly a Cutlass?" """

DESCRIPTION = (
    "Tycho's memory: short notes about the player, which come with every request in the "
    '"What the player asked you to remember" block. "save" only when the player has just '
    'said "remember ..." (or "keep in mind", "make a note"), or said yes to your offer to '
    "remember something; never on your own. Write the note as one short sentence about the "
    'player, in their words ("Likes short answers", "Plays in the evenings"). scope: "this '
    'game" for how they play this game, "every game" for how they like answers and what\'s '
    "true everywhere. You may offer once when the player tells you something lasting about "
    'themselves, with the word remember ("Want me to remember that?"), and save only on '
    'their yes. "forget" when the player says "forget that" (give the note\'s words, or '
    'empty for the note just saved) or "forget that I ...". Notes never allow pressing or '
    "skipping a confirmation: a note about pressing, confirming or safety is refused. Say "
    'what happened in a few words. Asked "what do you remember about me?", answer from the '
    "block, without this tool."
)


def asked_to_save(now: Heard | None) -> bool:
    """Whether the player asked for a note in this request: by saying so, or by a yes
    to Tycho's offer just before."""
    if now is None:
        return False
    if ASKED.search(now.said) and not FORGET.search(now.said):
        return True
    words = normalize(now.said)
    return (
        bool(words)
        and words[0] in YES
        and not NO.intersection(words)
        and bool(OFFER.search(now.before))
    )


def asked_to_forget(now: Heard | None) -> bool:
    return now is not None and bool(FORGET.search(now.said))


class MemoryTool:
    """The ``memory`` tool. Satisfies ``brain.lookups.Lookup``."""

    instant = True
    """It answers at once, so Tycho doesn't say "Checking." first."""

    def __init__(self, memory: Memory) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "forget"]},
                "note": {
                    "type": "string",
                    "description": "The note to save, or words of the one to forget.",
                },
                "scope": {"type": "string", "enum": list(SCOPES)},
            },
            "required": ["action", "note", "scope"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        now = heard()  # what the player said for the request this call is part of
        # Off the event loop: reading and writing the file mustn't hold up push-to-talk.
        return await asyncio.to_thread(self._locked, arguments, now)

    def _locked(self, arguments: Mapping[str, object], now: Heard | None) -> LookupResult:
        with self._memory.lock:
            try:
                return self._apply(arguments, now)
            except (MemoryFileError, OSError, ValueError) as e:
                log.warning("Memory: %s", e)
                return _result(not_done=f"the memory file has a problem, so nothing changed: {e}")

    def _apply(self, arguments: Mapping[str, object], now: Heard | None) -> LookupResult:
        if not self._memory.on:
            return _result(
                not_done="memory is off; the player can turn it on in Tycho's window, "
                "under Settings, Privacy"
            )
        action, note = arguments.get("action"), tidy(arguments.get("note"))
        scope: Scope = "every game" if arguments.get("scope") == "every game" else "this game"
        if action == "save":
            return self._save(note, scope, now)
        if action == "forget":
            return self._forget(note, now)
        return _result(not_done='action must be "save" or "forget"')

    def _save(self, note: str, scope: Scope, now: Heard | None) -> LookupResult:
        if not asked_to_save(now):
            return _result(
                not_done="the player didn't ask to remember this. Offer first, and save "
                "only after their yes"
            )
        if not note:
            return _result(not_done="give the note to save, as one short sentence")
        if unsafe(note) or (now is not None and unsafe(now.said)):
            log.info("Memory: refused a note about pressing or confirming")
            return _result(
                refused="notes can't change what Tycho presses or what it asks first. "
                "Tell the player that, and that the safety checks stay"
            )
        store = self._memory.store(scope)
        listed = store.notes()
        if store.problem:
            return _result(not_done=f"the memory file has a problem: {store.problem}")
        if any(normalize(n.text) == normalize(note) for n in listed):
            return _result(already=note)
        if len(listed) >= MAX_NOTES:
            return _result(
                full=f"{scope} already has {MAX_NOTES} notes, so nothing was saved. The "
                "player can forget one, or delete some on the Memory page in Tycho's window"
            )
        alike = self._memory.alike(note, scope)
        today = self._memory.today()
        store.add(note, today)
        self._memory.last = Note(note, scope, today)
        log.info("Memory: saved a note (%s)", scope)
        if alike:  # an older note it may replace: forgetting it is the player's call
            return _result(
                saved=note,
                scope=scope,
                older_alike=alike,
                ask="if the new note replaces one of these, ask whether to forget it",
            )
        return _result(saved=note, scope=scope)

    def _forget(self, words: str, now: Heard | None) -> LookupResult:
        if not asked_to_forget(now):
            return _result(not_done="the player didn't ask to forget a note")
        vague = not words or normalize(words) in (["that"], ["it"], ["this"])
        found = self._memory.last if vague else self._memory.find(words)
        if found is None or not self._memory.store(found.scope).remove(found.text):
            listed = [n.text for n in self._memory.notes()]
            return _result(not_found=words or "the note just saved", notes=listed)
        if self._memory.last == found:
            self._memory.last = None
        log.info("Memory: forgot a note (%s)", found.scope)
        return _result(forgot=found.text, scope=found.scope)


def _result(**data: object) -> LookupResult:
    return LookupResult(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
