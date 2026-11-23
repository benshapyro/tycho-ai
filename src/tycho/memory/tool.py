"""The brain's ``memory`` tool: save a note, or forget one (ADR 8, #483).

A lasting fact the player states about how they play this game is saved as they say
it, and Tycho says "Noted." (ADR 8's second amendment). Such a note must be about this
game, hold only words the player said in this request (``from_said``: nothing from a
look-up, a web page or Tycho's replies), and not be about them outside the game
(``PERSONAL``). Anything else is saved only when the player's words ask Tycho to
remember (``ASKED``), or are a yes to Tycho's offer in the line just before, inside the
follow-up window (``brain.heard``). Every check reads what the player said, not what the
brain claims they said. A note about pressing or confirming is refused
(``memory.unsafe``), whoever words it. Forgetting needs the player to have said so.
"""

import asyncio
import json
import logging
import re
from collections.abc import Mapping

from tycho.brain.heard import Heard, heard
from tycho.brain.lookups import LookupResult
from tycho.memory import (
    MAX_NOTES,
    PLAIN,
    SCOPES,
    Memory,
    MemoryFileError,
    Note,
    Scope,
    tidy,
    unsafe,
)
from tycho.text import normalize

log = logging.getLogger(__name__)

ASKED = re.compile(
    r"\b(?:remember|keep in mind|make a note|take a note|note that|don'?t forget)\b", re.I
)
"""Words that ask Tycho to keep something."""
QUESTION = re.compile(
    r"\b(?:what|anything|do you|did you)\b[^.!?]*\bremember\b"
    r"|\bcan you remember\s+(?:what|which|where|when|who|how|if|whether|anything)\b",
    re.I,
)
"""Asking what Tycho remembers isn't asking it to keep something. "Can you remember I
like short answers" is asking (#590); "can you remember what I fly" isn't."""
FORGET = re.compile(
    r"\b(?<!don't )(?<!dont )(?<!do not )"
    r"(?:forget|delete|remove|erase|scratch|stop remembering|don'?t remember)\b",
    re.I,
)
"""Words that ask Tycho to drop a note; "don't forget" asks it to keep one."""
_KEEP_BEFORE = re.compile(r"\b(?:don'?t|do not|never|not|keep)\b", re.I)
_CLAUSE = re.compile(r"[,.;!?]|\b(?:but|and|then)\b", re.I)


def asks_to_forget(said: str) -> bool:
    """Whether the player asks Tycho to drop something: a forget word with no "never",
    "don't ever" or "keep" before it in the same part of the sentence (#625)."""
    for match in FORGET.finditer(said):
        clause = _CLAUSE.split(said[: match.start()])[-1]
        if not _KEEP_BEFORE.search(clause):
            return True
    return False


PERSONAL = re.compile(
    r"\b(?:my (?:real )?name|i'?m called|years old|birthday|i live|live in|lives in|"
    r"my (?:wife|husband|partner|girlfriend|boyfriend|kids?|children|son|daughter|mum|"
    r"mom|dad|mother|father|family|job|boss|work|doctor|address|email|phone|number|"
    r"brothers?|sisters?|siblings?|friends?|cousins?|uncle|aunt|grand\w+|nan|age|"
    r"career|employer|office|school|uni|university|college|teacher|neighbou?rs?)|"
    r"i'?m from|i am from|i come from|i grew up|"
    r"i'?m (?:\d{1,2}(?!\s*\w)|(?:an? )?\d{1,2}[- ]?(?:years?|yrs?)(?:[- ]old| of age|"
    r"(?=\s*[.!?]?\s*$)))|"
    r"i work (?:as|at|in|for)|for a living|i'?m an? (?:nurse|teacher|doctor|student|"
    r"lawyer|accountant|programmer|developer|electrician|plumber|chef|cook|soldier)|"
    r"health|sick|medic\w*|diagnos\w*|disabilit\w*|adhd|autis\w*|depress\w*|anxiety|"
    r"pregnan\w*|religio\w*|politic\w*|real life|irl)\b",
    re.I,
)
"""About the player outside the game (who they are, where they're from, their age and
work, other people): saved only when they ask, never as they say it (ADR 8's second
amendment, #590). "Engineer" stays out: it's a role in Star Citizen."""
NOTE_WORDS = (
    PLAIN
    | {"home", "main", "base", "usual", "favourite", "favorite", "prefers"}
    | {
        "uses",
        "use",
        "runs",
        "wants",
    }
)
"""Words a stated fact's note may add to what the player said ("Home is Area18")."""
YES = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "absolutely", "definitely"}
    | {"correct", "right", "do", "go", "remember", "aye", "affirmative"}
)
"""The first word of a yes to the offer: "yes please", "sure, do it", "go ahead"."""
NO = frozenset({"no", "nope", "nah", "not", "never", "dont", "don"})
OFFER = re.compile(
    r"\b(?:want me to|shall i|should i|may i|can i|like me to)\s+(?:remember|keep|note)\b", re.I
)
"""Tycho's offer names what it does: "Want me to remember you fly a Cutlass?" """

DESCRIPTION = (
    "Tycho's memory: short notes about the player, which come with every request in the "
    '"What you know about the player" block. "save" at once, with scope "this game", when '
    "the player states a lasting fact about how they play this game: what they use, where "
    "they're based, their group, role or playstyle, how they like answers here. Not what "
    "they're doing just now, and not a question. Write the note as one short sentence "
    "about the player using only words they just said, plus small words "
    '("Plays support", "Usually plays solo"), never from a look-up, a web page or your own '
    'words; then say "Noted," and the note in a few words. Anything else (scope "every '
    'game", or about them outside the game: name, where they live, work, health, other '
    'people) only after they say "remember ..." or yes to your offer, which has the words '
    '"want me to remember" ("Want me to remember that?"). "forget" when the player says '
    '"forget that" (give the note\'s words, or empty for the note just saved) or "forget '
    'that I ...". Notes never allow pressing or skipping a confirmation: a note about '
    'pressing, confirming or safety is refused. Asked "what do you remember about me?", '
    "answer from the block, without this tool."
)


def asked_to_save(now: Heard | None) -> bool:
    """Whether the player asked for a note in this request: by saying so, or by a yes
    to Tycho's offer just before."""
    if now is None:
        return False
    if ASKED.search(now.said) and not asks_to_forget(now.said):
        return not QUESTION.search(now.said)
    words = normalize(now.said)
    offer = now.before.rpartition("said: ")[2] if "said: " in now.before else ""
    return (
        bool(words) and words[0] in YES and not NO.intersection(words) and bool(OFFER.search(offer))
    )


def from_said(note: str, said: str) -> bool:
    """Whether every word of ``note`` that matters is one the player said: "Uses a
    Cutlass" from "I'm in my Cutlass", not "Uses a Cutlass Black"."""
    heard_words = normalize(said)
    wanted = [w for w in normalize(note) if w not in NOTE_WORDS]
    return bool(wanted) and all(any(_same(w, h) for h in heard_words) for w in wanted)


def _same(a: str, b: str) -> bool:
    """The same word, give or take an ending: "trade" and "trading"."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def unasked_problem(note: str, scope: Scope, now: Heard | None) -> str:
    """Why ``note`` can't be saved without the player asking, or empty when it can."""
    if now is None or not from_said(note, now.said):
        return (
            "a note saved without being asked must be a fact the player just stated, in "
            "their own words. Otherwise offer first, and save only after their yes"
        )
    if scope != "this game" or PERSONAL.search(note) or PERSONAL.search(now.said):
        return (
            "only facts about how the player plays this game are saved without asking. "
            'Offer first ("Want me to remember that?"), and save only after their yes'
        )
    return ""


def asked_to_forget(now: Heard | None) -> bool:
    return now is not None and asks_to_forget(now.said)


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
        if not note:
            return _result(not_done="give the note to save, as one short sentence")
        if unsafe(note) or (now is not None and unsafe(now.said)):
            log.info("Memory: refused a note about pressing or confirming")
            return _result(
                refused="notes can't change what Tycho presses or what it asks first. "
                "Tell the player that, and that the safety checks stay"
            )
        asked = asked_to_save(now)
        if not asked and (problem := unasked_problem(note, scope, now)):
            return _result(not_done=problem)
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
        log.info("Memory: saved a note (%s, %s)", scope, "asked" if asked else "stated")
        tell = {} if asked else {"say": 'say "Noted," and the note in a few words'}
        if alike:  # an older note it may replace: forgetting it is the player's call
            return _result(
                saved=note,
                scope=scope,
                older_alike=alike,
                ask="if the new note replaces one of these, ask whether to forget it",
                **tell,
            )
        return _result(saved=note, scope=scope, **tell)

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
