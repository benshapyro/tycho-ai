"""Where things are on the star map's frame, from the words text recognition read
there (#588): the search box's line and column, and how near two spots are. For
``runtime.route``."""

from tycho.eyes.words import Reading, Word, squeezed


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


def under_header(
    reading: Reading, header: str, until: str, name: str, reach: float
) -> tuple[Word, ...] | None:
    """The line that is ``name`` in the list under the ``header`` words, before the line
    that holds ``until`` (run-together words too: "ALLRESULTS") and within ``reach`` of
    the frame's height, should ``until`` go unread; None if not there."""
    found = reading.find(header)
    if found is None:
        return None
    top = max(word.top + word.height for word in found)
    stop = squeezed(until).replace(" ", "")
    for line in sorted(reading.below(top).lines(), key=lambda line: line[0].top):
        if line[0].top > top + reach * reading.height:
            return None
        said = " ".join(word.text for word in line)
        if stop and stop in squeezed(said).replace(" ", ""):
            return None
        if alike(said, name):
            return line
    return None
