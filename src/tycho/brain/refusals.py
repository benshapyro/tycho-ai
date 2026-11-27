"""Acts a round refuses before anything is pressed, and what the model hears: actions
that aren't bound (``unbound``, #164), and a question answered with a press (#708).

A question is never answered with a press.

"What's coming in the next patch" was once answered by pressing two actions, with
nothing in the player's words asking for either. The model's pick is untrusted (ADR
14), so before an act is taken, ``asked_only`` checks the words heard: when they open
with a question word and name nothing to do (no action word, no word of the picked
actions' names), the act is refused and the model is told to answer in words.

A missed press is safer than a wrong one, so the test leans to refusing; requests that
open like a question ("what about the lights", "how about ...", "why not") are left
alone, and so is a clear yes to Tycho's own offer ("Want me to lower the gear?" "What
are you waiting for, do it": #715)."""

import difflib
import re
from collections.abc import Collection, Iterable

from tycho.brain.decisions import UnboundAction
from tycho.brain.heard import Heard
from tycho.brain.lookups import LookupResult, failure
from tycho.text import normalize

QUESTION_WORDS = frozenset(
    {"what", "whats", "when", "where", "wheres", "why", "who", "whos", "which", "how", "hows"}
)
"""First words that make the player's words a question."""
NOT_A_QUESTION = frozenset({("what", "about"), ("how", "about"), ("why", "not")})
"""Openings that ask for something, or say yes: "how about lights off", "why not"."""
ACTION_WORDS = frozenset(
    {"open", "close", "shut", "lock", "unlock", "raise", "lower", "turn", "toggle", "switch"}
    | {"press", "push", "engage", "disengage", "launch", "fire", "arm", "disarm", "deploy"}
    | {"retract", "eject", "request", "cycle", "target", "activate", "deactivate", "enable"}
    | {"disable", "mute", "unmute", "boost", "power"}
)
"""Words that ask Tycho to do something. Plain English, no game's words: a game's own
words reach the check through the picked actions' names. Not small words that
questions use too ("what's coming up", "how do I"), so a question isn't let through
for one."""
ANSWER_IN_WORDS = (
    "Not pressed: the player asked a question and didn't ask for this. Answer the "
    "question in words, or with show_card, and press nothing."
)
"""The result the model gets for an act refused this way."""


GENERIC = frozenset(
    {"system", "systems", "mode", "state", "level", "group", "set", "all", "view"}
    | {"reset", "fwd", "back", "next", "prev", "main", "default", "status", "current"}
)
"""Parts of action names too common to say the player asked for the action: "what
system am I in" doesn't name ``v_toggle_landing_system`` (review of #712, #714)."""


OFFER = re.compile(
    r"(?:^|[.!?]\s+)(?:(?:do you |would you )?want me to|shall i|should i)\b[^.!?]*\?\s*$",
    re.IGNORECASE,
)
"""Tycho's line ending in an offer to do something: "Want me to lower the gear?"."""
YES = frozenset(
    {("do", "it"), ("just", "do", "it"), ("yes",), ("yeah",), ("yep",), ("sure",)}
    | {("go", "ahead"), ("go", "for", "it"), ("please",), ("yes", "please"), ("hit", "it")}
    | {("please", "do"), ("do", "it", "then"), ("do", "it", "now"), ("yes", "do", "it")}
)
"""The last part of the player's words that says yes to an offer, on its own."""


HINT = "\n[Speech-to-text:"
"""Where the lexicon's note on a misheard word starts (``Corrected.for_brain``)."""


def player_said(heard: Heard) -> str:
    """The player's own words, without the lexicon's note on a misheard word."""
    return heard.said.partition(HINT)[0]


def says_yes_to_offer(heard: Heard) -> bool:
    """Whether Tycho's last line offered to do something and the player's words end in
    a clear yes: "what are you waiting for, do it" (#715). Only Tycho's own offer counts,
    and only a yes that stands alone after a break, so "why would I do it" isn't one."""
    offer = heard.before.rpartition("said: ")[2] if "said: " in heard.before else ""
    if not OFFER.search(offer.strip().replace("\u2019", "'")):
        return False
    parts = [p for p in re.split(r"[,.!?;:]+", player_said(heard)) if p.strip()]
    if len(parts) < 2:  # a yes alone doesn't open with a question word: never refused
        return False
    return tuple(normalize(parts[-1])) in YES


def asked_only(heard: Heard | None, picked: Iterable[str]) -> bool:
    """Whether the words ``heard`` are only a question, so no press answers them.
    ``picked`` are the act's action or sequence names, whose words count as asking for
    them ("what's my atc key, request it" names atc)."""
    if heard is None:
        return False
    words = normalize(player_said(heard))
    while words and words[0] in {"tycho", "hey", "ok", "okay", "so", "and", "um", "uh"}:
        words.pop(0)
    if not words or words[0] not in QUESTION_WORDS or tuple(words[:2]) in NOT_A_QUESTION:
        return False
    if set(words) & ACTION_WORDS or says_yes_to_offer(heard):
        return False
    return not names_any(words, picked)


def names_any(words: Iterable[str], picked: Iterable[str]) -> bool:
    """Whether ``words`` name one of the ``picked`` actions by a part of its name:
    "flight" names v_flightready, "atc" v_atc_request and "decoys" v_..._decoy_launch (a
    plural only: "powerful" doesn't name v_power_toggle, review of #718)."""
    named = [
        part
        for name in picked
        for part in name.lower().split("_")
        if len(part) > 2 and part not in GENERIC
    ]
    plural = {f"{n}s" for n in named} | {f"{n}es" for n in named}  # "decoys", not "powerful"
    return any(w == n or (len(w) > 3 and w in n) for w in words for n in named) or bool(
        plural.intersection(words)
    )


def unbound(error: UnboundAction, actions: Collection[str]) -> LookupResult:
    """What the model hears when it names actions that aren't bound: the closest that
    are, or that there's no key for it, so it says so."""
    close = {
        name: difflib.get_close_matches(name, actions, n=3, cutoff=0.5) for name in error.unknown
    }
    hints = "; ".join(
        f"{name}: did you mean {', '.join(found)}?" if found else f"{name}: nothing close is bound"
        for name, found in close.items()
    )
    return failure(
        f"Nothing was pressed. Not bound: {hints}. Pick from the bound actions, or say "
        "there's no key for it and what to bind."
    )
