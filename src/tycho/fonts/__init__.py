"""The fonts the window and the Tile draw with (ADR 17): Saira Semi Condensed, Saira
Condensed and Share Tech Mono, all under the SIL Open Font License. They ship with Tycho, so
nothing is fetched and every PC looks the same; Windows' own fonts stay as the fallback."""

from importlib import resources
from importlib.resources.abc import Traversable
from typing import Final

FILES: Final[dict[str, str]] = {
    "label": "SairaSemiCondensed-SemiBold.ttf",
    "text": "SairaSemiCondensed-Regular.ttf",
    "condensed": "SairaCondensed-SemiBold.ttf",
    "number": "ShareTechMono-Regular.ttf",
}
"""Each face's file in this folder."""


def path(face: str) -> Traversable:
    """The file for ``face`` (a key of ``FILES``)."""
    return resources.files(__package__).joinpath(FILES[face])
