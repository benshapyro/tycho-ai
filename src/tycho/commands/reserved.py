"""The session's own words: whole utterances it answers before any command is matched.

They live here, not in the runtime, so the player's ``commands.toml`` and the Commands
page can say when a phrase is one of them (#227): the session would answer it first,
so a command given it never runs.
"""

from collections.abc import Iterable
from functools import cache
from typing import Literal

from tycho.lexicon import Lexicon
from tycho.text import normalize

CONFIRM_WORDS = frozenset({"confirm", "confirmed", "affirmative"})
CANCEL_WORDS = frozenset({"cancel", "abort", "negative", "belay that", "never mind", "nevermind"})
"""Replies to a question. Whole utterances only, and no command may use them as phrases."""
TILE_WORDS: dict[str, frozenset[str]] = {
    "pin": frozenset(
        {
            "keep that up",
            "keep it up",
            "keep that",
            "pin that",
            "pin it",
            "leave that up",
            "keep that there",
            "keep the tile there",
            "keep the tile up",
            "keep the card up",
        }
    ),
    "clear": frozenset(
        {
            "clear",
            "clear that",
            "clear the tile",
            "clear tile",
            "hide that",
            "dismiss that",
            "hide the tile",
            "hide tile",
            "hide the card",
            "clear the card",
        }
    ),
    # Going back and forward through the cards that gave way (#491).
    "again": frozenset(
        {
            "show that again",
            "show it again",
            "bring that back",
            "bring it back",
            "go back a card",
            "back a card",
            "previous card",
            "last card",
            "show me the last card",
            "go back a tile",
            "back a tile",
            "previous tile",
            "last tile",
            "show me the last tile",
        }
    ),
    "forward": frozenset(
        {
            "go forward a card",
            "forward a card",
            "next card",
            "go forward a tile",
            "forward a tile",
            "next tile",
        }
    ),
    "show": frozenset(
        {"show me that", "show that", "show me", "put that on the tile", "put it on the tile"}
    ),
}
"""What the player says to the Tile (ADR 16), while it's on. Whole utterances only, so
"clear the target" is still the game's; no command may use them as phrases."""
SAY_AGAIN_WORDS = frozenset(
    {
        "say that again",
        "say it again",
        "say again",
        "repeat that",
        "repeat it",
        "come again",
    }
)
"""Asking to hear Tycho's last line again (#222, #226), Tile on or off. It's replayed
word for word, without asking the brain. Whole utterances only, and no command may use
them as phrases."""


type Use = Literal["say_again", "question", "tile"]

USES: dict[Use, str] = {
    "say_again": "hearing Tycho's last line again",
    "question": "answering Tycho's confirm questions",
    "tile": "the Tile",
}
"""What each kind of the session's words is for, in plain words."""


@cache
def _lexicon(terms: tuple[str, ...]) -> Lexicon:
    """The lexicon the session corrects what's heard with: the game pack's words."""
    return Lexicon(terms)


def reserved_for(phrase: str, terms: Iterable[str]) -> Use | None:
    """Which of the session's words ``phrase`` is, compared as the session compares
    what's said: after the correction by ``terms``, the game pack's words (#236). None
    if it isn't one."""
    for text in (phrase, _lexicon(tuple(terms)).correct(phrase).text):
        if (use := _use(" ".join(normalize(text)))) is not None:
            return use
    return None


def _use(said: str) -> Use | None:
    if said in SAY_AGAIN_WORDS:
        return "say_again"
    if said in CONFIRM_WORDS | CANCEL_WORDS:
        return "question"
    if any(said in words for words in TILE_WORDS.values()):
        return "tile"
    return None
