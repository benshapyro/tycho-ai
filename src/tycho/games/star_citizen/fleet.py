"""The player's fleet (ADR 15): the ships they own, and the one they're flying now.

The player says it ("I own a Cutlass Black", "I sold my Prospector", "I'm flying
the Carrack"), the brain calls ``update_fleet``, and the names are matched against
the ship data, so the fleet holds the game's names. The fleet is kept in
``fleet.toml`` in Tycho's settings folder, which the player can edit by hand; the
ship being flown lasts until they say otherwise or Tycho restarts.

Every request carries the fleet in its header (``describe``), next to the date,
so "what should I haul?" uses the player's real cargo space. Nothing here presses
a key: it only changes what Tycho knows about the player.
"""

import asyncio
import json
import logging
import os
import threading
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from tycho.brain.lookups import LookupResult, failure
from tycho.games.star_citizen.ships import Ambiguous, NotFound, Ship, ShipLibrary

log = logging.getLogger(__name__)

MAX_SHIPS = 100
HEADER = """\
# Your ships, for Tycho. Tycho adds and removes them when you tell it ("I own a
# Cutlass Black", "I sold my Prospector"). You can edit this file too: one name
# per line, as the game writes it.
"""
IMPORTED = """
# The ships your last hangar import added (tycho fleet import). Importing again
# replaces these and keeps the ships above that you added yourself.
"""

DESCRIPTION = """\
Update what Tycho knows about the player's ships. Call it when the player says \
they own, bought or pledged a ship (add), sold, melted or lost one (remove), or \
which ship they're in now (flying; "none" when they get out). Pass names as the \
player said them; pass empty values for anything unchanged. Never call it for \
ships the player is only asking about."""


class FleetFileError(OSError):
    """The fleet file exists but couldn't be read, so Tycho won't write over it."""


class Fleet:
    """The ships the player owns (kept in a file) and the one they're flying (not kept)."""

    def __init__(
        self,
        path: Path,
        ships: Sequence[str] = (),
        flying: str = "",
        problem: str = "",
        *,
        imported: Sequence[str] = (),
    ) -> None:
        self.path = path
        self.ships = list(ships)
        self.flying = flying
        # Set when the file is there but unreadable: saving would replace the player's
        # hand-written list with whatever was said this session, so ``save`` refuses.
        self.problem = problem
        self.imported = [name for name in imported if name in self.ships]
        """The entries of ``ships`` the last hangar import added (#556): the next
        import replaces these and leaves the rest, which the player added."""
        self._stamp = _stamp(path)

    @classmethod
    def load(cls, path: Path) -> Fleet:
        """The fleet in ``path``; an empty one if there's no file or it can't be read.

        An unreadable file is left alone (``problem`` says why) rather than overwritten.
        """
        try:
            # utf-8-sig: Notepad and PowerShell can save a byte-order mark, which TOML rejects.
            data = tomllib.loads(path.read_text("utf-8-sig"))
        except FileNotFoundError:
            return cls(path)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            return cls._unreadable(path, str(e))
        # Not a list: read as no ships, the next save would write over them (#612).
        if wrong := [k for k in ("ships", "imported") if not isinstance(data.get(k, []), list)]:
            return cls._unreadable(path, f"{wrong[0]} isn't a list of names in [ ]")
        # Every ship, even past MAX_SHIPS in a hand-written list: a save keeps them all.
        return cls(path, _strings(data.get("ships")), imported=_strings(data.get("imported")))

    @classmethod
    def _unreadable(cls, path: Path, why: str) -> Fleet:
        problem = (
            f"{path} can't be read ({why}), so Tycho won't change it. "
            "Fix that, or delete the file to start a new fleet."
        )
        log.warning("Starting without your fleet: %s", problem)
        return cls(path, problem=problem)

    def refresh(self) -> None:
        """Read the file again if something else changed it since (an import from the
        command line while Tycho runs), so the next save doesn't undo that change. A file
        that can't be read now (a hand edit) keeps the list in memory and is never saved
        over (#612) until it reads again."""
        if (stamp := _stamp(self.path)) == self._stamp:
            return
        fresh = Fleet.load(self.path)
        self._stamp = stamp
        if fresh.problem:
            self.problem = fresh.problem
            return
        self.ships, self.imported, self.problem = fresh.ships, fresh.imported, ""

    def remove(self, entry: str) -> None:
        """Take ``entry`` out of the fleet, and out of the last import's list."""
        self.ships.remove(entry)
        if entry in self.imported:
            self.imported.remove(entry)

    def save(self) -> None:
        """Write the fleet, swapping the new file in so a crash never loses it."""
        if self.problem:
            raise FleetFileError(self.problem)
        lines = [HEADER, "ships = [", *_listed(self.ships), "]"]
        if imported := [name for name in self.imported if name in self.ships]:
            lines += [IMPORTED, "imported = [", *_listed(imported), "]"]
        lines.append("")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".partial")
        partial.write_text("\n".join(lines), "utf-8")
        os.replace(partial, self.path)
        self._stamp = _stamp(self.path)

    def describe(self, library: ShipLibrary | None) -> str:
        """The fleet as one line for each request's header; empty if Tycho knows nothing."""
        self.refresh()
        if not self.ships and not self.flying:
            return ""
        parts: list[str] = []
        if self.ships:
            ships = self.ships[:MAX_SHIPS]  # the header's limit; the file keeps the rest
            parts.append("The player's ships: " + "; ".join(_brief(n, library) for n in ships))
        if self.flying:
            parts.append(f"Flying now: {_brief(self.flying, library)}")
        return "[" + ". ".join(parts) + "]"


def patch_line(library: ShipLibrary | None) -> str:
    """The game's patch, as one line for each request's header (#59), so the brain judges
    what's current by it, not by what it remembers; empty until the ship data is in."""
    if library is None or not library.patch:
        return ""
    if library.stale:
        return f"[Game patch: {library.patch} when last checked; today's couldn't be checked]"
    return f"[Game patch: {library.patch}]"


class FleetUpdate:
    """The ``update_fleet`` tool. Satisfies ``brain.lookups.Lookup``."""

    instant = True
    """It answers at once, so Tycho doesn't say "Checking." first."""

    def __init__(self, fleet: Fleet, library: Callable[[], ShipLibrary | None]) -> None:
        """``library`` gives the ship data once it has loaded (``ShipLookup.library``)."""
        self._fleet = fleet
        self._library = library
        self._changing = threading.Lock()  # two calls in one turn change one fleet in turn

    @property
    def name(self) -> str:
        return "update_fleet"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        names = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "properties": {
                "add": {**names, "description": "Ships the player now owns."},
                "remove": {**names, "description": "Ships the player no longer owns."},
                "flying": {
                    "type": "string",
                    "description": 'The ship they\'re in now, "none" if they got out, or empty.',
                },
            },
            "required": ["add", "remove", "flying"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        library = self._library()
        if library is None:
            return failure("the ship data is still loading; ask the player to say it again soon")
        # Off the event loop: matching names and writing the file mustn't hold up
        # push-to-talk (audit, 2026-11-03).
        return await asyncio.to_thread(self._apply, library, arguments)

    def _apply(self, library: ShipLibrary, arguments: Mapping[str, object]) -> LookupResult:
        with self._changing:
            return self._change(library, arguments)

    def _change(self, library: ShipLibrary, arguments: Mapping[str, object]) -> LookupResult:
        fleet = self._fleet
        fleet.refresh()
        changed: dict[str, list[str]] = {"added": [], "removed": []}
        problems: list[dict[str, object]] = []

        for asked in _names(arguments.get("add")):
            ship = _match(library, asked, problems)
            if ship is None or len(fleet.ships) >= MAX_SHIPS:
                continue
            if owned(library, fleet.ships, ship) is None:
                fleet.ships.append(ship.name)
                changed["added"].append(ship.name)
        for asked in _names(arguments.get("remove")):
            ship = _match(library, asked, problems)
            if ship is None:
                continue
            if (entry := owned(library, fleet.ships, ship)) is not None:
                fleet.remove(entry)
                changed["removed"].append(entry)
            else:
                problems.append({"asked": asked, "note": f"{ship.name} isn't in the fleet"})
        flying = str(arguments.get("flying") or "").strip()
        if flying.lower() in ("none", "nothing", "no ship"):
            fleet.flying = ""
        elif flying and (ship := _match(library, flying, problems)) is not None:
            fleet.flying = ship.name

        if changed["added"] or changed["removed"]:
            try:
                fleet.save()
            except OSError as e:
                log.warning("Couldn't save your fleet to %s: %s", fleet.path, e)
                problems.append(
                    {"note": f"the change couldn't be saved, so it lasts until restart: {e}"}
                )
        result: dict[str, object] = {
            **{k: v for k, v in changed.items() if v},
            "fleet": fleet.ships,
            "flying": fleet.flying or None,
        }
        if problems:
            result["problems"] = problems
        return LookupResult(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def _strings(value: object) -> list[str]:
    """A TOML list of names as strings; anything else is no names."""
    return [str(v) for v in cast(list[object], value)] if isinstance(value, list) else []


def _listed(names: Sequence[str]) -> list[str]:
    return [f"    {json.dumps(name, ensure_ascii=False)}," for name in names]


def _stamp(path: Path) -> tuple[int, int, int] | None:
    """When ``path`` last changed, its size and which file it is (None if it isn't
    there), to notice another writer. Two saves in one Windows clock tick share a time,
    but each save swaps in a new file (#574)."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def owned(library: ShipLibrary, ships: Sequence[str], ship: Ship) -> str | None:
    """The fleet's entry for ``ship``, by what each name means rather than how it's
    written: a hand-edited "Prospector" is the data's "MISC Prospector"."""
    for entry in ships:
        found = library.find(entry)
        if entry == ship.name or (isinstance(found, Ship) and found.key == ship.key):
            return entry
    return None


def _names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in cast(list[object], value) if str(v).strip()]


def _match(library: ShipLibrary, asked: str, problems: list[dict[str, object]]) -> Ship | None:
    found = library.find(asked)
    match found:
        case Ship():
            return found
        case Ambiguous():
            problems.append(
                {"asked": asked, "could_be": list(found.candidates), "note": "ask which"}
            )
        case NotFound():
            problems.append({"asked": asked, "closest": list(found.closest), "note": "not found"})
    return None


def _brief(name: str, library: ShipLibrary | None) -> str:
    """A ship as the header says it: its name, cargo, and role."""
    found = library.find(name) if library is not None else None
    if not isinstance(found, Ship):
        return name
    facts = found.facts
    details = [str(v) for v in (facts.get("role") or facts.get("roles"),) if v]
    if isinstance(cargo := facts.get("cargo_scu"), int | float) and cargo:
        details.append(f"{cargo:g} SCU")
    if found.is_concept:
        details.append("concept, not flyable yet")
    return f"{name} ({', '.join(details)})" if details else name
