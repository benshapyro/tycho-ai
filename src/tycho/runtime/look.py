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


def asks_to_look(text: str, phrases: Sequence[str]) -> bool:
    """Whether ``text`` holds one of ``phrases``, word for word, anywhere in it, and
    doesn't say not to (``NOT_NOW``)."""
    words = normalize(text)
    if NOT_NOW.intersection(words):
        return False
    said = f" {' '.join(words)} "
    return any(f" {' '.join(normalize(p))} " in said for p in phrases if normalize(p))


def says_yes(text: str) -> bool:
    """Whether the whole of ``text`` is a "yes", as an answer to the offer to look."""
    return " ".join(normalize(text)) in YES_WORDS


def wants_a_look(text: str, eyes: Eyes | None, *, offered: bool) -> bool:
    """Whether the player asked to look: a look phrase, or "yes" just after an offer."""
    if eyes is None:
        return False
    return asks_to_look(text, eyes.phrases) or (offered and says_yes(text))


async def look(eyes: Eyes | None, on: bool, feedback: Feedback) -> Picture | Unseen:
    """One picture of the game window, if looking is ``on``, with the camera sound; or
    why there's none. Taken off the event loop, and never kept here."""
    if not on:
        return Unseen("refused", "looking at the game window is off", OFF)
    if eyes is None:
        return Unseen("failed", "this game has no window to look at", NOT_SEEN)
    try:
        picture = await asyncio.to_thread(eyes.camera.take)
    except CaptureError as e:
        log.warning("No picture of the game window: %s", e)
        return Unseen("failed", f"no picture: {e}", NOT_SEEN)
    feedback.camera()  # every capture is heard (ADR 14)
    log.info("Took a picture of the game window (%d bytes, %dx%d)", *_shape(picture))
    return picture


def _shape(picture: Picture) -> tuple[int, int, int]:
    return picture.size, picture.width, picture.height
