"""Looking at the game window when the player asks (ADR 14's amendment, #31). Out of
``session.py``: the session asks ``wants_a_look`` where the words go, and ``look``
for the picture.

The code decides when Tycho looks, never the model. A look is asked for when what the
player said holds one of the pack's look phrases ("take a look"), or is a plain "yes"
within the follow-up window after the brain offered one (``LOOK_OFFER``). Then, only
with looking turned on (``look_at_screen``), one picture of the game window is taken,
the camera sound plays, and the picture goes to the brain with the words. Off, Tycho
says so and where to turn it on, and nothing is taken or sent.

A look only answers. If the brain picks keys to press in a request with a picture,
nothing is pressed (``ANSWER_ONLY``): nothing in a picture can press a key.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from tycho.eyes import CaptureError, Picture
from tycho.runtime.messages import Progress, Result, Status, Trace
from tycho.runtime.ports import Camera, Feedback
from tycho.text import normalize

log = logging.getLogger(__name__)

LOOK = "look"
"""How a look shows in the log and the action log: the picture never does."""
YES_WORDS = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "yes please", "please do", "go ahead", "go for it"}
    | {"ok", "okay", "do it", "look"}
)
"""A whole utterance that says yes to the brain's offer to look, soon after it."""
OFF = "Looking at your game is off. You can turn it on in Tycho's window, under Settings, Privacy."
NOT_SEEN = "I can't see the game window. Is the game open, and not minimised?"


@dataclass(frozen=True, slots=True)
class Eyes:
    """What the session needs to look: the pack's look phrases (``look`` in
    ``pack.toml``) and the camera on its game window. None for a pack with no window."""

    phrases: tuple[str, ...]
    camera: Camera


@dataclass(frozen=True, slots=True)
class Unseen:
    """Why there's no picture: the request ends here, with a line for the player."""

    status: Status
    detail: str
    say: str

    def result(
        self, progress: Progress, request: int, trace: Trace, heard: str, text: str
    ) -> Result:
        """How the request ends: Tycho's own line, not the brain's."""
        progress.by_brain, progress.say = False, self.say
        return progress.result(request, self.status, trace, heard, text, self.detail)


ANSWER_ONLY = Unseen(
    "refused",
    "a look only answers",
    "I only look to answer questions, so I didn't press anything. Say the command on its own.",
)
"""The brain picked keys to press in a request with a picture: nothing is pressed."""


NOT_NOW = frozenset({"not", "dont", "stop", "never", "off", "disable", "without"})
"""Words that turn a look phrase around ("don't look at my screen", "stop looking"):
never a look. That goes to the brain, which can turn looking off (``settings``)."""
NOT_NOW_REACH = 4
"""How many words before a look phrase, in the same clause, a ``NOT_NOW`` word turns it
around. Further away, or after it ("take a look, is my shield off?"), it's about
something else (#554)."""
_CLAUSES = re.compile(r"[,.;:!?]+")


def asks_to_look(text: str, phrases: Sequence[str]) -> bool:
    """Whether ``text`` holds one of ``phrases``, word for word, anywhere in it, and
    doesn't say not to just before it (``NOT_NOW``, ``NOT_NOW_REACH``)."""
    wanted = [words for words in (normalize(p) for p in phrases) if words]
    return any(_asked(normalize(clause), wanted) for clause in _CLAUSES.split(text))


def _asked(words: list[str], phrases: list[list[str]]) -> bool:
    return any(
        words[start : start + len(phrase)] == phrase
        and not NOT_NOW.intersection(words[max(0, start - NOT_NOW_REACH) : start])
        for phrase in phrases
        for start in range(len(words) - len(phrase) + 1)
    )


def says_yes(text: str) -> bool:
    """Whether the whole of ``text`` is a "yes", as an answer to the offer to look."""
    return " ".join(normalize(text)) in YES_WORDS


FILLER = frozenset(
    {"tycho", "please", "hey", "ok", "okay", "can", "could", "would", "you", "go", "ahead"}
)
"""Words around a look phrase that still leave it only a look ("tycho, take a look please")."""


def only_a_look(text: str, phrases: Sequence[str]) -> bool:
    """Whether all of ``text`` is a look: a "yes", or one look phrase with at most
    ``FILLER`` around it. "Take a look at what Laranite sells for" is a question too."""
    said = " ".join(normalize(text))
    if said in YES_WORDS:
        return True
    for phrase in filter(None, (" ".join(normalize(p)) for p in phrases)):
        before, found, after = f" {said} ".partition(f" {phrase} ")
        if found and FILLER.issuperset((before + " " + after).split()):
            return True
    return False


def wants_a_look(text: str, eyes: Eyes | None, offered: bool, on: bool = True) -> bool:
    """Whether the player asked to look: a look phrase, or "yes" just after an offer.
    With looking off, only a request that is all a look is (to say it's off): a
    question that also says "take a look" goes on to be answered as before."""
    if eyes is None:
        return False
    asked = asks_to_look(text, eyes.phrases) or (offered and says_yes(text))
    return asked and (on or only_a_look(text, eyes.phrases))


async def look(
    eyes: Eyes | None, on: bool, feedback: Feedback, text: str = "", wanted: bool = True
) -> Picture | Unseen | None:
    """One picture of the game window, if looking is ``on``, with the camera sound; or
    why there's none. Taken off the event loop, and never kept here. None when no look
    was ``wanted``, or with no picture and more asked than a look (``only_a_look``):
    the brain answers without one."""
    if not wanted:
        return None
    if not on:
        return Unseen("refused", "looking at the game window is off", OFF)
    if eyes is None:
        return Unseen("failed", "this game has no window to look at", NOT_SEEN)
    try:
        picture = await asyncio.to_thread(eyes.camera.take)
    except Exception as e:  # anything, in the frozen build too: the player hears why (#552)
        if isinstance(e, CaptureError):
            log.warning("No picture of the game window: %s", e)
        else:
            log.exception("Taking a picture of the game window failed")
        if not only_a_look(text, eyes.phrases):
            return None
        return Unseen("failed", f"no picture: {e}", NOT_SEEN)
    feedback.camera()  # every capture is heard (ADR 14)
    log.info("Took a picture of the game window (%d bytes, %dx%d)", *_shape(picture))
    return picture


def _shape(picture: Picture) -> tuple[int, int, int]:
    return picture.size, picture.width, picture.height
