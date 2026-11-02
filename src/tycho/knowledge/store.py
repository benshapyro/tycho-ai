"""The local copy of the ship data: one read-only SQLite file per patch (ADR 15).

Built on the player's PC from the Star Citizen Wiki API on first run and when a
new patch goes live, in Tycho's data folder (``paths.knowledge_dir``). Never in
the repository: the API's data has no licence to redistribute it.

``ensure`` is the one entry point. It checks which patch is current, uses the
copy for it if there is one, downloads one if not, and falls back to the newest
copy on disk (marked stale) when the API can't be reached.

Ships still in concept come from UEX (``concepts``). If UEX can't be reached, the
copy is kept without them, and the next start fetches just those.
"""

import asyncio
import datetime
import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path

from tycho.knowledge.ships import Ship, ShipLibrary, concepts_from_uex, ships_from_wiki
from tycho.knowledge.uex import concept_vehicles
from tycho.knowledge.wiki import WikiError, WikiSource, all_vehicles, current_version

log = logging.getLogger(__name__)

SCHEMA = 2
"""Bump when the facts kept change, so old copies are rebuilt."""
PREFIX = "ships-"


class ShipDataError(Exception):
    """There's no ship data on this PC, and it couldn't be downloaded."""


async def ensure(
    source: WikiSource,
    folder: Path,
    today: Callable[[], datetime.date] = datetime.date.today,
    *,
    concepts: WikiSource | None = None,
) -> ShipLibrary:
    """``concepts`` is UEX, for the ships still in concept; without it there are none."""
    try:
        version = await current_version(source)
    except WikiError as e:
        return await _fallback(folder, f"can't check the current patch ({e})")
    path = file_for(folder, version)
    library: ShipLibrary | None = None
    if await asyncio.to_thread(path.exists):
        try:
            library = await asyncio.to_thread(load, path)
        except (sqlite3.Error, ValueError, KeyError) as e:
            log.warning("The ship data in %s is unreadable (%s); downloading it again", path, e)
    changed = library is None
    if library is None:
        try:
            ships = ships_from_wiki(await all_vehicles(source))
        except WikiError as e:
            return await _fallback(folder, f"couldn't download the {version} ship data ({e})")
        library = ShipLibrary(ships, version=version, as_of=today().isoformat())
    if concepts is not None and not library.concepts:
        try:
            found = concepts_from_uex(await concept_vehicles(concepts), library.ships)
        except WikiError as e:
            log.warning("Ship data: no concept ships this time (%s)", e)
        else:
            library, changed = library.with_concepts(found), True
    if changed:
        await asyncio.to_thread(
            save, path, library.ships, version, library.as_of, concepts=library.concepts
        )
        await asyncio.to_thread(_remove_others, folder, path)
        log.info(
            "Ship data: %d ships and vehicles for %s, saved to %s", len(library), version, path
        )
    return library


def file_for(folder: Path, version: str) -> Path:
    return folder / f"{PREFIX}{re.sub(r'[^A-Za-z0-9._-]', '_', version)}.sqlite"


def save(
    path: Path, ships: Iterable[Ship], version: str, as_of: str, *, concepts: bool = False
) -> None:
    """Write the whole copy to a temporary file, then swap it in, so a crash
    halfway never leaves a broken copy behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    with closing(sqlite3.connect(partial)) as db, db:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE ships (key TEXT PRIMARY KEY, name TEXT NOT NULL, maker TEXT NOT NULL,"
            " names TEXT NOT NULL, facts TEXT NOT NULL)"
        )
        db.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("schema", str(SCHEMA)),
                ("version", version),
                ("as_of", as_of),
                ("source", "api.star-citizen.wiki"),
                ("concepts", "yes" if concepts else "no"),
            ],
        )
        db.executemany(
            "INSERT OR REPLACE INTO ships VALUES (?, ?, ?, ?, ?)",
            [
                (s.key, s.name, s.maker, json.dumps(s.names), json.dumps(dict(s.facts)))
                for s in ships
            ],
        )
    os.replace(partial, path)


def load(path: Path, *, stale: bool = False) -> ShipLibrary:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema") != str(SCHEMA):
            raise ValueError(f"schema {meta.get('schema')}, expected {SCHEMA}")
        rows = db.execute("SELECT key, name, maker, names, facts FROM ships").fetchall()
    ships = [
        Ship(key=k, name=n, maker=m, names=tuple(json.loads(ns)), facts=json.loads(f))
        for k, n, m, ns, f in rows
    ]
    return ShipLibrary(
        ships,
        version=meta["version"],
        as_of=meta["as_of"],
        stale=stale,
        concepts=meta.get("concepts") == "yes",
    )


async def _fallback(folder: Path, problem: str) -> ShipLibrary:
    for path in await asyncio.to_thread(_copies, folder):
        try:
            library = await asyncio.to_thread(load, path, stale=True)
        except sqlite3.Error, ValueError, KeyError:
            continue
        log.warning("Ship data: %s, so using the copy for %s", problem, library.version)
        return library
    raise ShipDataError(f"no ship data on this PC, and {problem}")


def _copies(folder: Path) -> list[Path]:
    """The copies on disk, newest first."""
    return sorted(folder.glob(f"{PREFIX}*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)


def _remove_others(folder: Path, keep: Path) -> None:
    for old in folder.glob(f"{PREFIX}*.sqlite"):
        if old != keep:
            try:
                old.unlink()
            except OSError as e:
                log.debug("Couldn't remove old ship data %s: %s", old, e)
