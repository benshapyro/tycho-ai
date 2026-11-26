"""Which line of the star map's search panel is the chosen place (#588, #682): its own
panel after Enter, a row under RECENT, or the exact row among the results. From the words
text recognition read; for ``runtime.route``."""

from tycho.eyes.words import Reading, Word, squeezed
from tycho.packs.routes import RouteFlow
from tycho.runtime.mapwords import LISTED, alike, close_under, column, listed, row, shows

Line = tuple[Word, ...] | None
"""A line of words read, or None where there's none."""
SECTION = " system"
"""The end of a list's section header, which names a star system ("STANTON SYSTEM")."""
ROWS = 0.6
"""How far under its header, as a fraction of the frame's height, a results row is read."""


def detail(reading: Reading, box: tuple[Word, ...], flow: RouteFlow, name: str) -> Line:
    """The chosen place's name as a line of its own just under the box: the panel Enter
    opens for one result (26 Nov 2026), not a row of a results list, nor its name inside
    a description."""
    if listed(reading, box, flow):  # a list's rows aren't the panel
        return None
    for line in close_under(reading, box, LISTED).lines():
        if alike(" ".join(w.text for w in line), name):
            return line
    return None


def recent(
    reading: Reading, box: tuple[Word, ...], flow: RouteFlow, name: str, system: str | None
) -> Line:
    """The place under RECENT, when Enter picked it and emptied the box: its empty words
    back or no words at all, no filtered list (Ben's Orison and Hurston tries). Only in
    the player's system: RECENT keeps places from other systems (Ben's Nyx-side Pyro
    Gateway, #682)."""
    if not flow.recent or shows(row(reading, box), name) or reading.has(flow.no_results):
        return None
    return under_header(_under(reading, box), flow.recent, flow.results, name, LISTED, system)


def exact(
    reading: Reading, box: tuple[Word, ...], flow: RouteFlow, name: str, system: str | None
) -> Line:
    """The row that is exactly ``name`` among the results the typed name filtered, in the
    player's system's section: Enter would take the top one, "MicroTech Logistics Depot"
    for MicroTech (#682)."""
    if reading.has(flow.no_results) or (flow.recent and reading.has(flow.recent)):
        return None
    return under_header(_under(reading, box), flow.results, "", name, ROWS, system)


def under_header(
    reading: Reading, header: str, until: str, name: str, reach: float, system: str | None
) -> Line:
    """The line that is ``name`` in the list under the ``header`` words, before the line
    that holds ``until`` (run-together words too: "ALLRESULTS") and within ``reach`` of
    the frame's height, should ``until`` go unread; None if not there. With ``system``,
    not in another system's section ("NYX SYSTEM")."""
    found = reading.find(header)
    if found is None:
        return None
    top = max(word.top + word.height for word in found)
    stop = squeezed(until).replace(" ", "")
    here, section = squeezed(system or ""), ""
    for line in sorted(reading.below(top).lines(), key=lambda line: line[0].top):
        if line[0].top > top + reach * reading.height:
            return None
        said = " ".join(word.text for word in line)
        if stop and stop in squeezed(said).replace(" ", ""):
            return None
        if squeezed(said).endswith(SECTION):
            section = squeezed(said).removesuffix(SECTION).strip()
        elif alike(said, name) and not (here and section and section != here):
            return line
    return None


def _under(reading: Reading, box: tuple[Word, ...]) -> Reading:
    return column(reading.below(max(w.top + w.height for w in box)), box, reading)
