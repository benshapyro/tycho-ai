"""Words that name an action without asking Tycho for it, refused before anything is
pressed whatever the model picked (the trick phrasings eval, 27 Nov 2026).

Both adapters pressed for words like these, 10 to 12 times in 70: "the doors are open"
opened them, "Mike, lower your gear" lowered the player's own, "he said open the doors"
opened them, "if we get jumped, pop decoys" fired one at once, and "is my ship flight
ready" pressed flight ready. The model's pick is untrusted (ADR 14), so ``unasked``
checks the player's own words, as ``refusals.asked_only`` does for questions (#708):

- talking to someone else: a name and a comma first ("Mike, ...", "hey Sam, ...");
- passing on someone's words: "he said ...", "the tutorial says ...";
- saying how things are: "the doors are open", "we're in quantum", "he's opening...";
- something for later: "if ...", "once ...", "when ...": Tycho never waits to act;
- a yes-or-no question: "is ...", "can I ...", "should we ...", or words said as one:
  "lights on?" may ask whether they are (#717).

A request after a break still presses ("the doors are open, close them"), except after
"if", "once" or "when", where the part after the break is what to do later. So does a
clear yes to Tycho's own offer (#715). A missed press is safer than a wrong one: the
player says it again."""

import re
from collections.abc import Collection, Iterable, Mapping
from typing import cast

from tycho.brain.heard import Heard
from tycho.brain.refusals import (
    ACTION_WORDS,
    ANSWER_IN_WORDS,
    QUESTION_WORDS,
    asked_only,
    names_any,
    player_said,
    says_yes_to_offer,
)
from tycho.text import normalize, states

FILLER = frozenset({"tycho", "hey", "ok", "okay", "so", "um", "uh", "er", "oh", "and", "well"})
"""Words before the ones that say what the player wants."""
NOT_A_NAME = FILLER | {
    *("yes", "yeah", "yep", "no", "now", "right", "alright", "copy", "roger", "sure", "cool"),
    *("good", "great", "nice", "perfect", "thanks", "quick", "actually", "also", "first"),
    *("then", "next", "finally", "please", "fine", "wait", "listen", "look", "sorry", "damn"),
    *("shit", "crap", "ah", "hmm", "whoa", "go", "guys", "everyone", "all", "you", "like"),
    *("computer", "taiko", "tyco", "tiko", "tyko", "teko", "psycho", "tycoe"),
}
"""Words that can open a request with a comma after them, so aren't a name: with the
ways speech-to-text spells "Tycho", and "computer" (#719)."""
NAMED = re.compile(r"^\W*(?:(?:hey|ok|okay|oh|so|and)\W+)*([A-Z][A-Za-z'-]*)\s*,")
"""A capitalised word and a comma first: someone the player is talking to."""
THIRD = frozenset({"he", "hes", "she", "shes", "they", "someone", "somebody", "everybody"})
"""Words that open words about someone else."""
SAYS = frozenset(
    {"said", "says", "told", "tells", "yelled", "yells", "yelling", "shouted", "shouting"}
    | {"asked", "asks", "wants", "wanted", "saying", "telling", "reckons", "thinks"}
)
"""Verbs that pass on someone else's words: "my buddy says lower the gear"."""
BE = frozenset({"is", "are", "was", "were"})
LATER = frozenset({"if", "once", "when", "whenever", "until", "unless"})
"""Words that open something to do later, never now: Tycho has no timers (AGENTS.md)."""
YES_OR_NO = frozenset(
    {("is",), ("are",), ("was",), ("were",), ("does",), ("did",), ("am", "i")}
    | {("can", "i"), ("could", "i"), ("should", "i"), ("should", "we"), ("do", "i")}
    | {("will", "it"), ("would", "it"), ("is", "it"), ("do", "we", "need")}
)
"""Openings of a yes-or-no question. Not "can you" or "can we", which ask for things."""
ASKS_FOR = frozenset({("can", "i", "get"), ("could", "i", "get"), ("can", "i", "have")})
"""Yes-or-no openings that ask for something: "can I get the lights on"."""
LET_OFF = frozenset({("if", "you"), ("when", "you"), ("once", "you")})
"""Later-words that ask politely: "once you're ready, lower the gear" (#719)."""
ASKS_NOW = frozenset({("make", "sure"), ("ensure",)})
"""Openings that ask for how things should be: "make sure the gear is down" (#719)."""
REQUEST = frozenset(
    {("can", "you"), ("could", "you"), ("would", "you"), ("will", "you"), ("can", "we")}
    | {("could", "we"), ("lets",), ("let", "us"), ("how", "about"), ("what", "about")}
)
"""Openings that ask for something even said with a question mark: "can you open the
doors?". Speech-to-text writes "?" after these too."""
ASKING = frozenset({"should", "do", "does", "did", "is", "can", "will", "would", "are"})
"""A word after "when" that makes it a question, not something for later."""

SOMEONE_ELSE = (
    "Not pressed: the player was talking to someone else, or passing on their words, not "
    "asking you. Answer in words if it needs one, and press nothing."
)
HOW_THINGS_ARE = (
    "Not pressed: the player said how things are, not what to do. Answer in words, and "
    "press nothing."
)
NOT_NOW = (
    "Not pressed: the player said what to do later, and you never act later on your own. "
    "Say they can ask when it's time, and press nothing."
)


def unasked(heard: Heard | None, picked: Iterable[str], words: Collection[str] = ()) -> str:
    """Why the words ``heard`` don't ask for the act that would press ``picked``, as the
    model hears it, or "" when they do. ``words``: every offered action, mapped to the
    words players use for it (``spoken``) when known, so a short request after a break
    counts ("we're landing, gear down")."""
    if heard is None or says_yes_to_offer(heard):
        return ""
    picked = tuple(picked)
    used: Mapping[str, Collection[str]] = {}
    if isinstance(words, Mapping):
        used = cast(Mapping[str, Collection[str]], words)
    known = {w for name in picked for w in used.get(name, ())}
    said = player_said(heard).strip().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    clauses = [c for c in re.split(r"[,.!?;:]+", said) if normalize(c)]
    if not clauses:
        return ""
    first = _opening(normalize(clauses[0]))
    then = [w for c in clauses[1:] for w in normalize(c)]
    if first and first[0] in LATER and first[:2] not in LET_OFF:
        # "if you can, lower the gear" asks politely; "when should I..." is a question.
        return ANSWER_IN_WORDS if first[1:2] and first[1] in ASKING else NOT_NOW
    if (name := NAMED.match(said)) and name.group(1).lower() not in NOT_A_NAME:
        return SOMEONE_ELSE
    if _passes_on(first):
        return SOMEONE_ELSE
    if ACTION_WORDS.intersection(then) or names_any(then, picked) or known.intersection(then):
        return ""  # "the doors are open, close them"; "it's dark, lights on"
    if first and first[0] in THIRD:
        return SOMEONE_ELSE
    stated = states(clauses[0]) or BE.intersection(first[1:4])
    if first and first[0] not in QUESTION_WORDS | BE and stated and not _opens(first, ASKS_NOW):
        return HOW_THINGS_ARE
    if first[:3] not in ASKS_FOR and _opens(first, YES_OR_NO):
        return ANSWER_IN_WORDS
    if asked_only(heard, picked) or sounds_asked(said):
        return ANSWER_IN_WORDS
    return ""


def sounds_asked(said: str) -> bool:
    """Whether ``said`` ends in "?" without asking for anything (#717): "lights on?" or
    "doors open?" may ask whether they are, and pressing a toggle then does the
    opposite. "Can you open the doors?", "open the doors?" (a request word first) and
    "lights on, please?" ask for something."""
    if not said.rstrip().endswith("?"):
        return False
    words = _opening(normalize(said))
    if not words or "please" in words or words[0] in ACTION_WORDS:
        return False
    return not _opens(words, REQUEST | ASKS_FOR | ASKS_NOW)


def _passes_on(first: tuple[str, ...]) -> bool:
    """Whether the first words pass on someone else's: "my buddy says lower the gear",
    but not "I said lower the gear" or "like I said, lower the gear" (#719). "I think
    he said ..." is someone else's."""
    return any(w in SAYS and first[k - 1] != "i" for k, w in enumerate(first[1:6], start=1))


def _opens(words: tuple[str, ...], openings: Collection[tuple[str, ...]]) -> bool:
    return any(words[: len(o)] == o for o in openings)


def _opening(words: list[str]) -> tuple[str, ...]:
    while words and words[0] in FILLER:
        words = words[1:]
    return tuple(words)
