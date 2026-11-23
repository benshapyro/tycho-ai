"""The Memory page (ADR 8, #483): every note Tycho keeps, for every game and for the
chosen game, with a way to delete each, and beside them what else Tycho knows about
the player (their ships and what to call them), read-only: those keep their own places.
For Star Citizen, also the blueprints the player owns (ADR 8's third amendment, #598),
each with a way to take it off."""

import logging

from tycho import games, paths, settings
from tycho.address import called
from tycho.desktop.bridge.state import State
from tycho.memory import MAX_NOTES, SCOPES, Memory, MemoryFileError, NoteFile
from tycho.packs import STAR_CITIZEN, GamePack, PackError, built_in

log = logging.getLogger(__name__)


class MemoryPage(State):
    def memory(self) -> dict[str, object]:
        """The notes (each with its scope, the day it was saved, and whether it reads as
        an order about pressing, which Tycho leaves out), and the fleet and name."""
        current = settings.load(self._path)
        pack = _pack(current.game)
        memory = _memory(pack)
        notes = [
            {
                "text": n.text,
                "scope": n.scope,
                "saved": n.saved.isoformat() if n.saved else "",
                "unsafe": n.unsafe,
            }
            for n in memory.notes()
        ]
        problems = [p for p in (memory.store(s).problem for s in SCOPES) if p]  # set by reading
        try:
            ships = games.fleet_ships(pack)
        except OSError:
            ships = None
        return {
            "on": current.memory,
            "game_name": pack.name,
            "notes": notes,
            "problems": problems,
            "most": MAX_NOTES,
            "files": {
                "every game": str(paths.memory_file()),
                "this game": str(paths.game_memory_file(pack.id)),
            },
            "fleet": ships,
            "fleet_label": (games.hangar_hint(pack) or {}).get("label", ""),
            "call_me": called(current),
            "blueprints": _blueprints(pack),
        }

    def forget_note(self, scope: object, text: object) -> dict[str, object]:
        """Delete one note, by its scope and its words exactly as listed."""
        if scope not in SCOPES or not isinstance(text, str):
            return {"ok": False, "problem": "That note isn't one Tycho keeps."}
        memory = _memory(_pack(settings.load(self._path).game))
        store = memory.store("every game" if scope == "every game" else "this game")
        try:
            with memory.lock:  # the brain may be saving to the same file
                removed = store.remove(text)
        except (MemoryFileError, OSError) as e:
            return {"ok": False, "problem": f"Couldn't change the notes: {e}"}
        if not removed:
            return {"ok": False, "problem": "That note was already gone."}
        log.info("Memory: a note deleted on the Memory page (%s)", scope)
        return {"ok": True}

    def forget_blueprint(self, name: object) -> dict[str, object]:
        """Take one blueprint off the list, by its name exactly as listed."""
        owned = games.owned_blueprints(_pack(settings.load(self._path).game))
        if owned is None or not isinstance(name, str):
            return {"ok": False, "problem": "That blueprint isn't one Tycho keeps."}
        try:
            removed = owned.remove(name)
        except OSError as e:
            return {"ok": False, "problem": f"Couldn't change the list: {e}"}
        if not removed:
            return {"ok": False, "problem": "That blueprint was already gone."}
        log.info("Memory: a blueprint taken off on the Memory page")
        return {"ok": True}


def _blueprints(pack: GamePack) -> dict[str, object] | None:
    """The blueprints the player owns, as the list's file has them, or None for a game
    without them. Shown whatever ``memory`` says, like the notes."""
    owned = games.owned_blueprints(pack)
    if owned is None:
        return None
    listed = owned.listed()
    return {
        "file": str(owned.path),
        "problem": owned.problem,
        "list": [
            {"name": o.name, "got": o.got.isoformat() if o.got else "", "from": o.source}
            for o in listed
        ],
    }


def _pack(game: str) -> GamePack:
    try:
        return built_in(game)
    except PackError:  # tycho check and the status page say why
        log.exception("The chosen game's pack didn't load")
        return built_in(STAR_CITIZEN)


def _memory(pack: GamePack) -> Memory:
    """The chosen game's notes and every game's, read fresh from their files."""
    return Memory(NoteFile(paths.memory_file()), NoteFile(paths.game_memory_file(pack.id)))
