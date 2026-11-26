"""Where things are on the star map's frame, from the words text recognition read
there (#588): the search box's line and column, and how near two spots are. For
``runtime.route``."""

import re

from tycho.eyes.words import Reading, Word, squeezed
from tycho.packs.routes import RouteFlow


def middle(words: tuple[Word, ...]) -> tuple[int, int]:
    left = min(word.left for word in words)
    right = max(word.right for word in words)
    top = min(word.top for word in words)
    bottom = max(word.top + word.height for word in words)
    return (left + right) // 2, (top + bottom) // 2


def column(reading: Reading, box: tuple[Word, ...], frame: Reading) -> Reading:
    """Only the words in the search panel's column, under its box: the results list,
    not a marker on the map with the same name (review of #636)."""
    left = min(word.left for word in box) - round(0.03 * frame.height)
    right = max(word.right for word in box) + round(0.35 * frame.height)
    kept = tuple(w for w in reading.words if left <= w.left and w.right <= right)
    return Reading(reading.width, reading.height, kept, reading.window)


def close_under(reading: Reading, box: tuple[Word, ...], share: float) -> Reading:
    """The words of the search panel's column within ``share`` of the frame's height
    under the box."""
    bottom = max(word.top + word.height for word in box)
    under = column(reading.below(bottom), box, reading)
    near = [w for w in under.words if w.top <= bottom + share * reading.height]
    return Reading(reading.width, reading.height, tuple(near), reading.window)


def row(reading: Reading, box: tuple[Word, ...]) -> Reading:
    """Only the words on the search box's own line: what's typed in it (#654)."""
    top = min(word.top for word in box)
    bottom = max(word.top + word.height for word in box)
    slack = (bottom - top) // 2
    across = column(reading, box, reading)
    kept = tuple(w for w in across.words if top - slack <= w.middle[1] <= bottom + slack)
    return Reading(reading.width, reading.height, kept, reading.window)


def shows(reading: Reading, name: str) -> bool:
    """Whether a line of ``reading`` holds ``name``, compared as squeezed text, so
    "ARC-L1" read as one word still matches, and a caret read as a letter after the
    typed name doesn't hide it (review of #655)."""
    wanted = squeezed(name).replace(" ", "")
    return bool(wanted) and any(
        wanted in squeezed(" ".join(w.text for w in line)).replace(" ", "")
        for line in reading.lines()
    )


def starts(reading: Reading, start: str) -> bool:
    """Whether a word read starts with ``start``: "CALCULATIN@..." is "calculatin"."""
    return bool(start) and any(
        word.startswith(start) for w in reading.words for word in squeezed(w.text).split()
    )


def near(point: tuple[int, int], spot: tuple[int, int], reach: float) -> bool:
    return abs(point[0] - spot[0]) <= reach and abs(point[1] - spot[1]) <= reach


def alike(said: str, name: str) -> bool:
    """Whether a line read as ``said`` is ``name``, letting text recognition misread one
    letter of a name of five letters or more ("ORISOW" is Orison, 26 Nov 2026). Never a
    digit: "ARC-L2" is not ARC-L1."""
    got, wanted = squeezed(said).replace(" ", ""), squeezed(name).replace(" ", "")
    if got == wanted:
        return True
    if len(got) != len(wanted) or sum(c.isalpha() for c in wanted) < 5:
        return False
    slips = [i for i, (a, b) in enumerate(zip(got, wanted, strict=True)) if a != b]
    if len(slips) != 1 or not (got[slips[0]].isalpha() and wanted[slips[0]].isalpha()):
        return False
    start = 0
    for word in squeezed(name).split():  # "Pyro I" is not Pyro V (review of #680)
        if start <= slips[0] < start + len(word) and set(word) <= set("ivxl"):
            return False
        start += len(word)
    return True


SYSTEM_SUFFIX = re.compile(r"\s*\([^()]*\)\s*$")
"""UEX tells same-named places apart by their system ("Pyro Gateway (Stanton)"): the
map's search, which only covers the player's system, knows it as "Pyro Gateway" (#682)."""


def map_name(name: str) -> str:
    """``name`` as the map's search knows it: without UEX's "(System)" at its end."""
    return SYSTEM_SUFFIX.sub("", name).strip() or name


def close_spot(reading: Reading, box: tuple[Word, ...], flow: RouteFlow) -> tuple[int, int] | None:
    """The search panel's close button: the x read on the box's line right of where its
    words were (there once the box has the keys, 26 Nov 2026), or where the pack's offset
    puts it from the box, if text recognition agrees (the offset was measured at
    2560x1440; on a 5120x2160 screen the x sat nearer). None if neither."""
    said = {mark.lower() for mark in flow.close_marks}
    right = max(word.right for word in box)
    line = [w for w in row(reading, box).words if w.text.strip().lower() in said]
    if marks := [w for w in line if w.left > right]:
        return min(marks, key=lambda word: word.left).middle
    height, (x, y) = reading.height, middle(box)
    spot = (round(x + flow.close_offset[0] * height), round(y + flow.close_offset[1] * height))
    reach = flow.close_reach * height
    for word in reading.words:
        if word.text.strip().lower() in said and near(word.middle, spot, reach):
            return spot
    return None


LISTED = 0.15
"""How far under the box, as a fraction of the frame's height, its list starts."""


def listed(reading: Reading, box: tuple[Word, ...], flow: RouteFlow) -> bool:
    """The panel's list is open under the box: "RECENT" or "ALL RESULTS" in its column,
    as once the box has the keys (Ben's PC try, 26 Nov 2026)."""
    close = close_under(reading, box, LISTED)
    return any(word and close.has(word) for word in (flow.recent, flow.results))


def took(
    before: Reading, after: Reading, box: tuple[Word, ...], first: str, flow: RouteFlow
) -> bool:
    """The first letter landed: a new word on the box's line that starts with it (not
    a caret read as "I", review of #672), or the RECENT list gone while the panel still
    shows (text recognition may not read one letter)."""
    marks = {mark.lower() for mark in flow.close_marks}
    was = {squeezed(w.text) for w in row(before, box).words}
    for word in row(after, box).words:
        text = squeezed(word.text)
        if text and text not in marks and text not in was and text.startswith(first):
            return True
    gone = bool(flow.recent) and before.has(flow.recent) and not after.has(flow.recent)
    return gone and listed(after, box, flow)


_COMMON = frozenset({"the", "of", "on", "at", "station", "port", "city", "outpost", "base"})
"""Words in a place's name too common to tell the route line's id it's this place."""


def id_names(route_id: str, name: str) -> bool:
    """Whether the log's route id ("levski_all-001") names the place ("Levski"): one of
    the name's own words (not ``_COMMON``, at least 3 letters) is one of the id's.
    Until Tycho reads the game's own id list, a route it can't match is reported as not
    checked, never as done (ADR 3, 24 Nov 2026)."""
    parts = set(re.split(r"[^a-z0-9]+", route_id.lower()))
    words = [w for w in squeezed(name).split() if len(w) >= 3 and w not in _COMMON]
    return bool(words) and any(word in parts for word in words)
