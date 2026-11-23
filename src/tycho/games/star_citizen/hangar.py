"""Filling the player's fleet from a hangar export (#556): ccugame.app's ``ships.csv``
or the ``fleetview.json`` it and other fleet tools write.

Phase 1 is ships only. Each name is matched against the ship data as ``update_fleet``
matches a spoken one, and goes in the same ``fleet.toml`` (ADR 15), so the brain sees
the player's hangar in every request's header with no new tool.

Privacy: only the ship's name (and, in ``ships.csv``, whether it's in the buy-back
list) is read. The account, pledge ids, prices, dates and the ship's own name are
never kept or logged, and nothing leaves the PC. Every name is untrusted text:
cleaned and cut here, and only ever matched against the ship data, never shown to the
brain as it was written.

Importing again replaces what the last import added (``Fleet.imported``) and keeps
the ships the player added by voice or by hand.
"""

import csv
import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from tycho.games.star_citizen.fleet import MAX_SHIPS, Fleet, FleetFileError, owned
from tycho.games.star_citizen.ships import Ambiguous, NotFound, Ship, ShipLibrary

LABEL = "Your ships"
HINT = (
    "Add the ships in your hangar from ccugame.app: export your hangar there, then "
    "choose ships.csv or fleetview.json. Tycho shows what changes before it saves, "
    "and keeps only the ship names, never your account or what you paid."
)
"""The Settings page's words for the import (the page names no game: #364)."""
MAX_BYTES = 5_000_000
"""Bigger than any hangar export; a larger file isn't one."""
MAX_ROWS = 5000
MAX_NAME = 80
"""The longest ship name kept; real ones are under 50 letters."""
LEFT_OUT_PLACES = frozenset({"buyback"})
"""``ships.csv``'s ``place`` values for ships the player doesn't have now."""
SHIP_TYPES = frozenset({"ship", "vehicle", ""})
"""``fleetview.json``'s ``type`` values that are ships; others (paints, items) are left out."""


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u206f]")


class HangarFileError(ValueError):
    """The file isn't a ships export Tycho can read; the message says why, for the player."""


@dataclass
class HangarPlan:
    """What an import would change, to show the player before anything is saved."""

    read: int
    """How many ships the file lists."""
    ships: list[str]
    """The fleet after the import."""
    imported: list[str]
    """What the import's own list will be after it."""
    added: list[str] = field(default_factory=list[str])
    kept: list[str] = field(default_factory=list[str])
    """In the file and already in the fleet."""
    gone: list[str] = field(default_factory=list[str])
    """The last import added these and this file doesn't have them: they come off."""
    unknown: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, tuple[str, ...]]]
    )
    """Names that match no ship, with the closest ones."""
    unsure: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, tuple[str, ...]]]
    )
    """Names that fit several ships equally, with those ships."""
    over: list[str] = field(default_factory=list[str])
    """Ships left out because the fleet holds ``MAX_SHIPS`` at most."""

    @property
    def changes(self) -> bool:
        return bool(self.added or self.gone)


@dataclass
class Prepared:
    """An import read and planned, waiting for the player's yes."""

    fleet: Fleet
    plan: HangarPlan
    lines: list[str]
    """What it would do, in plain lines."""
    library: ShipLibrary | None = None
    names: list[str] = field(default_factory=list[str])
    """The file's ship names, to plan again at ``save``."""

    @property
    def changes(self) -> bool:
        return self.plan.changes

    @property
    def done(self) -> str:
        return (
            f"Saved. Your fleet has {_count(len(self.plan.ships), 'ship')}, "
            "and Tycho uses them from now on."
        )

    def save(self) -> None:
        """Save it, planned again against the fleet as it is now: a ship said by voice
        while the plan was on screen stays (#574). ``OSError`` if the fleet file can't be
        read or written."""
        if self.library is not None:
            self.fleet.refresh()
            if self.fleet.problem:
                raise FleetFileError(self.fleet.problem)
            self.plan = plan(self.fleet, self.library, self.names)
        apply(self.fleet, self.plan)


def prepare(fleet_file: Path, file: Path, library: ShipLibrary) -> Prepared:
    """Read ``file`` and plan its import into the fleet in ``fleet_file``. Changes
    nothing. ``HangarFileError`` if the export can't be read, ``FleetFileError`` if the
    fleet file is there but broken (it's never written over)."""
    fleet = Fleet.load(fleet_file)
    if fleet.problem:
        raise FleetFileError(fleet.problem)
    names = read_export(file)
    chosen = plan(fleet, library, names)
    return Prepared(fleet, chosen, summary(chosen, file.name), library, names)


def read_export(path: Path) -> list[str]:
    """The ship names in a ``ships.csv`` or ``fleetview.json`` export, cleaned, in order."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise HangarFileError(f"{path.name} is too big to be a hangar export.")
        text = path.read_text("utf-8-sig")
    except UnicodeDecodeError:
        raise HangarFileError(f"{path.name} isn't a text file, so it can't be an export.") from None
    except OSError as e:
        raise HangarFileError(f"{path.name} couldn't be opened: {e.strerror or e}.") from e
    if text.lstrip().startswith("["):
        return _from_fleetview(text, path.name)
    return _from_csv(text, path.name)


def plan(fleet: Fleet, library: ShipLibrary, names: Sequence[str]) -> HangarPlan:
    """What importing ``names`` would do to ``fleet``. Changes nothing."""
    found: list[Ship] = []
    unknown: list[tuple[str, tuple[str, ...]]] = []
    unsure: list[tuple[str, tuple[str, ...]]] = []
    for name in names:
        match library.find(name):
            case Ship() as ship:
                if all(ship.key != f.key for f in found):  # two of one ship is one entry
                    found.append(ship)
            case Ambiguous(candidates=candidates):
                if all(asked != name for asked, _ in unsure):
                    unsure.append((name, candidates))
            case NotFound(closest=closest):
                if all(asked != name for asked, _ in unknown):
                    unknown.append((name, closest))

    # What the last import added and this file still has stays; the rest comes off.
    entries = {id(ship): owned(library, fleet.ships, ship) for ship in found}
    still = {entry for entry in entries.values() if entry is not None}
    gone = [e for e in fleet.imported if e in fleet.ships and e not in still]
    ships = [e for e in fleet.ships if e not in gone]
    imported = [e for e in fleet.imported if e in ships]
    result = HangarPlan(len(names), ships, imported, gone=gone, unknown=unknown, unsure=unsure)
    for ship in found:
        if (entry := entries[id(ship)]) is not None and entry in ships:
            result.kept.append(entry)
        elif len(ships) >= MAX_SHIPS:
            result.over.append(ship.name)
        else:
            ships.append(ship.name)
            imported.append(ship.name)
            result.added.append(ship.name)
    return result


def apply(fleet: Fleet, chosen: HangarPlan) -> None:
    """Save ``chosen`` to the fleet. ``OSError`` (a ``FleetFileError`` among them) if it
    can't be saved; the fleet in memory is then as it was."""
    before = fleet.ships, fleet.imported
    fleet.ships, fleet.imported = list(chosen.ships), list(chosen.imported)
    try:
        fleet.save()
    except OSError:
        fleet.ships, fleet.imported = before
        raise


def summary(chosen: HangarPlan, file: str) -> list[str]:
    """The plan in plain lines, for the player to agree to."""
    lines = [f"{file} lists {_count(chosen.read, 'ship')}."]
    if chosen.added:
        lines.append(f"Adds {len(chosen.added)}: {', '.join(chosen.added)}.")
    if chosen.kept:
        lines.append(f"Already in your fleet: {', '.join(chosen.kept)}.")
    if chosen.gone:
        lines.append(
            f"Takes off {len(chosen.gone)} your last import added that this file doesn't have: "
            f"{', '.join(chosen.gone)}."
        )
    for asked, closest in chosen.unknown:
        near = f" The closest are {', '.join(closest)}." if closest else ""
        lines.append(f'Left out "{asked}": it matches no ship.{near}')
    for asked, could in chosen.unsure:
        lines.append(f'Left out "{asked}": it could be {" or ".join(could)}.')
    if chosen.over:
        lines.append(
            f"Your fleet holds {MAX_SHIPS} ships at most, so these are left out: "
            f"{', '.join(chosen.over)}."
        )
    if chosen.unknown or chosen.unsure:
        lines.append('Tell Tycho any left out by voice ("I own a ...") if you have them.')
    if not chosen.changes:
        lines.append("Nothing to change: your fleet already matches this file.")
    return lines


def _from_csv(text: str, file: str) -> list[str]:
    try:
        reader = csv.DictReader(io.StringIO(text))
        columns = set(reader.fieldnames or ())
        # The ships file has both; the paint, equipment and upgrade files don't.
        if not {"name", "insurance"} <= columns:
            raise HangarFileError(
                f"{file} isn't a ships export. Choose ships.csv or fleetview.json from "
                "ccugame.app's export."
            )
        names: list[str] = []
        for number, row in enumerate(reader):
            if number >= MAX_ROWS:
                break
            # Only these two columns are read: the account, pledge and prices never are.
            if str(row.get("place") or "").strip().lower() in LEFT_OUT_PLACES:
                continue
            if name := _name(row.get("name")):
                names.append(name)
    except csv.Error as e:
        raise HangarFileError(f"{file} couldn't be read as a spreadsheet: {e}.") from e
    return names


def _from_fleetview(text: str, file: str) -> list[str]:
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError:
        raise HangarFileError(f"{file} couldn't be read: it isn't a fleetview file.") from None
    if not isinstance(data, list):
        raise HangarFileError(f"{file} couldn't be read: it isn't a fleetview file.")
    names: list[str] = []
    for entry in cast(list[object], data)[:MAX_ROWS]:
        if not isinstance(entry, dict):
            continue
        entry = cast(dict[str, object], entry)
        if str(entry.get("type") or "").strip().lower() not in SHIP_TYPES:
            continue
        if name := _name(entry.get("name")):
            names.append(name)
    return names


def _name(value: object) -> str:
    """A name from the file as safe, short text; empty if there's nothing to match."""
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL.sub(" ", value).split())[:MAX_NAME].strip()


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
