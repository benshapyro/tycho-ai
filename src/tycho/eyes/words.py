"""The words on the game window, with where each one is (#588, ADR 14's amendment "a
local check before a press").

Windows' own text recognition reads them on the PC, in the capture's child process
(``eyes.child``), and only the words and their boxes come back: never the picture. They
are never sent anywhere or kept. A reading can stop a press, or aim one of the route
flow's clicks (ADR 3's amendments for #588), but never start one.

What the game shows is untrusted (a marker can carry another player's name), so a
reading is only ever compared with text Tycho already chose: it's never said, logged in
full, or given to the brain.
"""

import json
from dataclasses import dataclass

from tycho.eyes import CaptureError

MAX_WORDS = 800
"""More words than a full star map shows: anything past this is dropped."""
MAX_TEXT = 60
"""The longest one word may be."""


@dataclass(frozen=True, slots=True)
class Word:
    """One word, and its box in the frame's pixels (from the frame's top left)."""

    text: str
    left: int
    top: int
    width: int
    height: int
    line: int
    """Which line of text it's on, counting from 0: words on one line share it."""

    @property
    def middle(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    @property
    def right(self) -> int:
        return self.left + self.width


@dataclass(frozen=True, slots=True)
class Reading:
    """What one frame of the game window said, and how big the frame was."""

    width: int
    height: int
    words: tuple[Word, ...] = ()
    window: int = 0
    """The game window it was read from (its handle), so a click aimed from it goes to
    that window or nowhere. Set by the reader, not the child."""

    def lines(self) -> tuple[tuple[Word, ...], ...]:
        """The words, line by line, in the order they were read."""
        found: dict[int, list[Word]] = {}
        for word in self.words:
            found.setdefault(word.line, []).append(word)
        return tuple(tuple(words) for words in found.values())

    def find(self, phrase: str) -> tuple[Word, ...] | None:
        """The words that say ``phrase`` in a row on one line (case and dots ignored), the
        first place it's found; None if nowhere."""
        wanted = squeezed(phrase).split()
        if not wanted:
            return None
        for words in self.lines():
            said = [squeezed(word.text) for word in words]
            for start in range(len(said) - len(wanted) + 1):
                if said[start : start + len(wanted)] == wanted:
                    return words[start : start + len(wanted)]
        return None

    def has(self, phrase: str) -> bool:
        return self.find(phrase) is not None

    def below(self, top: int) -> Reading:
        """Only the words whose middle is lower in the frame than ``top``."""
        kept = tuple(w for w in self.words if w.middle[1] > top)
        return Reading(self.width, self.height, kept, self.window)


def squeezed(text: str) -> str:
    """ "SEARCH LOCATIONS..." is "search locations": lower case, letters, digits and the
    spaces between words. What text recognition reads as dots or marks is left out."""
    kept = "".join(c if c.isalnum() else " " for c in text.lower())
    return " ".join(kept.split())


def encode(reading: Reading) -> bytes:
    """``reading`` as the child sends it on stdout."""
    words = [[w.text, w.left, w.top, w.width, w.height, w.line] for w in reading.words]
    return json.dumps({"width": reading.width, "height": reading.height, "words": words}).encode()


def decode(sent: bytes) -> Reading:
    """What the child sent, checked: anything malformed is a ``CaptureError``."""
    try:
        data = json.loads(sent)
        width, height = int(data["width"]), int(data["height"])
        words = tuple(_word(item) for item in data["words"][:MAX_WORDS])
    except (ValueError, KeyError, TypeError, IndexError) as e:
        raise CaptureError("the text recognition sent nothing usable") from e
    if width <= 0 or height <= 0:
        raise CaptureError("the text recognition sent no frame size")
    return Reading(width, height, words)


def _word(item: object) -> Word:
    if not isinstance(item, list):
        raise ValueError("not a word")
    parts: list[object] = list(item)  # pyright: ignore[reportUnknownArgumentType]
    if len(parts) != 6 or not isinstance(text := parts[0], str):
        raise ValueError("not a word")
    numbers = [n for n in parts[1:] if isinstance(n, int) and not isinstance(n, bool) and n >= 0]
    if len(numbers) != 5:
        raise ValueError("not a word")
    return Word(text[:MAX_TEXT], *numbers)
