"""Actions the brain presses only when the player's words single them out (#648).

In play, "switch laser tighten" (speech-to-text's guess at something) pressed Toggle Laser
Beam (High / low), the mining laser's module, and Tycho said "Toggling the laser beam,
high or low." A line in the briefing said to ask about words it can't place, and the
model did, mostly: in a later run it pressed again. The model's pick is untrusted (ADR
14), so for the actions a pack lists as ``named`` in ``safety.toml``, ``unnamed`` checks
the player's own words before anything is pressed:

- a word that's in no offered action's words ("tighten", "try"): Tycho can't tell what
  was meant, so it asks;
- words that another offered action has too ("mining laser" is the laser's fire, its
  type and its module): it asks which;
- no word of the action at all: it asks.

Small words, fillers and plain request words ("switch", "turn") never count as a word
Tycho can't place. A missed press is safer than a wrong one: the player says which."""

from collections.abc import Collection, Iterable, Mapping

from tycho.brain.briefing import SMALL
from tycho.brain.refusals import ACTION_WORDS
from tycho.text import normalize

FILLER = frozenset({"tycho", "hey", "ok", "okay", "so", "um", "uh", "er", "oh", "and", "well"})
"""Words before the ones that say what the player wants (``unasked`` uses them too)."""
SAYS_NOTHING = FILLER | {
    *("yes", "yeah", "yep", "right", "now", "just", "again", "quick", "quickly", "actually"),
    *("mate", "buddy", "man", "dude", "bro", "thanks", "thank", "you", "can", "could", "would"),
    *("will", "i", "we", "lets", "let", "need", "want", "like", "go", "ahead", "do", "that"),
    *("this", "those", "these", "one", "time", "for", "me"),
}
"""Words that say nothing about which action, so never a word Tycho can't place."""
ENDINGS = ("s", "es", "ing", "ed", "d")
"""Word endings dropped to find an action's word: "switching", "beams", "lowered"."""
WHICH_ONE = (
    "Not pressed: the player's words don't say which action they mean. Ask which, in a "
    "few words, and press nothing."
)
"""The result the model gets for an act refused this way."""


def unnamed(
    said: str, picked: Iterable[str], words: Mapping[str, Collection[str]], named: Collection[str]
) -> str:
    """``WHICH_ONE`` when ``picked`` holds a ``named`` action that the player's words
    ``said`` don't single out among the offered actions' ``words`` (``spoken``), or ""."""
    listed = [name for name in picked if name in named]
    if not listed:
        return ""
    content = [w for w in normalize(said) if w not in SMALL | SAYS_NOTHING]
    everyone = {w for name_words in words.values() for w in name_words}
    if any(w not in ACTION_WORDS and not _has(everyone, w) for w in content):
        return WHICH_ONE  # "switch laser tighten": what's "tighten"?
    for name in listed:
        hits = [w for w in content if _has(words.get(name, ()), w)]
        others = (other for other in words if other != name)
        if not hits or any(all(_has(words[o], w) for w in hits) for o in others):
            return WHICH_ONE  # "the mining laser": its fire, its type or its module?
    return ""


def _has(action_words: Collection[str], word: str) -> bool:
    """Whether ``word``, or it without a common ending, is one of ``action_words``."""
    if word in action_words:
        return True
    stems = (word[: -len(e)] for e in ENDINGS if word.endswith(e) and len(word) - len(e) >= 3)
    return any(stem in action_words or f"{stem}e" in action_words for stem in stems)
