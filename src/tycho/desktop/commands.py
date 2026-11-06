"""The app's Commands page (ADR 17): what the player can say, and their own phrases.

The list joins the built-in commands, the player's commands.toml and their keybinds:
each command's phrases (the built-in ones and the player's), the key it presses, and
for one with no key, the game action to bind.

Adding a phrase to a built-in command writes a copy of that command into the player's
commands.toml, with their phrase added: an entry with a built-in's id replaces it
(ADR 14, and the tycho-commands skill). Removing the player's last phrase from such a
copy removes the copy, so the built-in is back as it was. The file is edited with
tomlkit, which keeps the player's own entries and comments as they are, and a change is
written only after the new file loads as Tycho would load it. Changes apply when
Tycho next starts.
"""

# tomlkit's containers are loosely typed.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import os
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, cast

import tomlkit
import tomlkit.exceptions
from tomlkit.items import AoT, Array, Table

from tycho.commands import CommandFileError, CommandMatcher, resolve
from tycho.game.keybinds import Keybinds, KeyInput
from tycho.text import normalize

MAX_PHRASE = 60
NEW_FILE = "# Your own Tycho commands (ADR 14). Tycho's window adds to this file too.\n"


@dataclass(frozen=True, slots=True)
class Row:
    id: str
    label: str
    """What it does, in the game's words ("Landing System")."""
    said: list[str]
    """The built-in phrases still its own (a phrase the player gave elsewhere is gone)."""
    mine: list[str]
    """The player's phrases for it, from commands.toml."""
    key: str = ""
    """The key it presses ("Right Alt + O"); empty when none of its actions has one."""
    toggle: bool = False
    """It presses a toggle, a fallback, because its best action has no key."""
    to_bind: str = ""
    """With no key: the game action to bind."""
    confirm: bool = False
    sequence: bool = False
    steps: list[str] = field(default_factory=list[str])
    """A sequence's steps, with any waits."""


class Commands:
    def __init__(self, user_file: Path, keybinds: Callable[[], Keybinds | None]) -> None:
        """``keybinds`` gives the player's keybinds, or None if the game wasn't found."""
        self._path = user_file
        self._keybinds = keybinds

    # --- reading -------------------------------------------------------------------

    def rows(self) -> dict[str, object]:
        """Every command and sequence, for the page; and the file's problem, if any."""
        problem = ""
        try:
            matcher = CommandMatcher.default(self._path)
        except CommandFileError as e:
            problem = f"Your commands file can't be used, so only the built-in ones are: {e}"
            matcher = CommandMatcher.default()
        keybinds = self._keybinds()
        mine = _phrases(self._user_entries())
        rows: list[Row] = []
        for command in matcher.commands:
            yours = mine.get(command.id, [])
            said = [p for p in command.say if p not in yours]
            if command.is_sequence:
                steps = [
                    (f"wait {s.wait_seconds:g} s, then " if s.wait_seconds else "")
                    + s.command.id.replace(
                        "_", " "
                    ).capitalize()  # its own name: game labels can be shared
                    for s in command.steps
                ]
                rows.append(
                    Row(
                        command.id,
                        command.id.replace("_", " ").capitalize(),
                        said,
                        yours,
                        confirm=command.confirm,
                        sequence=True,
                        steps=steps,
                    )
                )
                continue
            label = _label(command.id, command.actions, keybinds)
            if keybinds is None:
                rows.append(Row(command.id, label, said, yours, confirm=command.confirm))
                continue
            found = resolve(command, keybinds)
            if found.action is None:
                rows.append(
                    Row(command.id, label, said, yours, to_bind=label, confirm=command.confirm)
                )
                continue
            key = ", ".join(key_text(k) for k in found.action.inputs if k.pressable)
            rows.append(
                Row(
                    command.id,
                    label,
                    said,
                    yours,
                    key=key,
                    toggle=found.fallback,
                    confirm=command.confirm,
                )
            )
        rows.sort(key=lambda r: (r.sequence, r.label.lower()))
        return {
            "rows": [_plain(r) for r in rows],
            "file": str(self._path),
            "problem": problem,
            "warnings": list(matcher.warnings),
            "game": keybinds is not None,
        }

    def _user_entries(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            data = tomllib.loads(self._path.read_text("utf-8-sig"))
        except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
            return {}
        return _by_id(data)

    # --- changing --------------------------------------------------------------------

    def add_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """Give a command one more phrase, the player's own."""
        said = _clean(phrase)
        if not said:
            return {"ok": False, "problem": "Type the words you'll say."}
        if len(said) > MAX_PHRASE:
            return {"ok": False, "problem": f"Keep it under {MAX_PHRASE} characters."}
        try:
            doc, entry = self._entry(command_id, create=True)
        except (OSError, UnicodeDecodeError, tomlkit.exceptions.TOMLKitError) as e:
            return {
                "ok": False,
                "problem": f"Your commands file has a mistake; fix it first ({e}).",
            }
        if entry is None:
            return {"ok": False, "problem": f"There's no command called {command_id!r}."}
        say = entry.get("say")
        if not isinstance(say, Array):
            say = tomlkit.array()
            entry["say"] = say
        if said in [str(p) for p in say]:
            return {"ok": False, "problem": f"“{said}” is already one of its phrases."}
        say.append(said)
        took = self._owner(said, command_id)
        result = self._write(doc)
        if result["ok"] and took:
            result["took_from"] = took
        return result

    def remove_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """Take away one of the player's own phrases."""
        said = _clean(phrase)
        try:
            doc, entry = self._entry(command_id, create=False)
        except (OSError, UnicodeDecodeError, tomlkit.exceptions.TOMLKitError) as e:
            return {
                "ok": False,
                "problem": f"Your commands file has a mistake; fix it first ({e}).",
            }
        say = entry.get("say") if entry is not None else None
        if not isinstance(say, Array) or said not in [str(p) for p in say]:
            return {"ok": False, "problem": "That isn't one of your phrases."}
        del say[[str(p) for p in say].index(said)]  # tomlkit arrays keep their comments
        built_in = _built_in().get(str(command_id))
        if entry is not None and built_in is not None and _is_plain_copy(entry, built_in):
            _drop(doc, str(command_id))  # nothing of the player's left: the built-in is back
        return self._write(doc)

    def _entry(self, command_id: object, *, create: bool) -> tuple[Any, Table | None]:
        """The player's file as a document, and its entry for ``command_id``; with
        ``create``, a copy of the built-in one when the file has none yet."""
        text = self._path.read_text("utf-8-sig") if self._path.exists() else NEW_FILE
        doc = tomlkit.parse(text)
        if not isinstance(command_id, str):
            return doc, None
        for kind in ("command", "sequence"):
            tables = doc.get(kind)
            if isinstance(tables, AoT):
                for table in tables:
                    if table.get("id") == command_id:
                        return doc, table
        built_in = _built_in().get(command_id)
        if built_in is None or not create:
            return doc, None
        kind = str(built_in["_kind"])
        table = tomlkit.table()
        table.comment(f"{command_id}: the built-in command, with your phrases (Tycho's window)")
        for key, value in built_in.items():
            if key != "_kind":
                table[key] = value
        tables = doc.get(kind)
        if not isinstance(tables, AoT):
            doc[kind] = tomlkit.aot()
            tables = doc[kind]
        cast(AoT, tables).append(table)
        return doc, cast(AoT, tables)[-1]

    def _owner(self, said: str, command_id: object) -> str:
        """The built-in command that had this phrase, which it now leaves (ADR 14)."""
        words = tuple(normalize(said))
        for entry_id, entry in _built_in().items():
            if entry_id != command_id and any(
                tuple(normalize(str(p))) == words for p in entry.get("say", [])
            ):
                return entry_id
        return ""

    def _write(self, doc: Any) -> dict[str, object]:
        """Write the file if Tycho can load it; otherwise say why and change nothing."""
        text = tomlkit.dumps(doc)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".commands-", suffix=".toml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            try:
                CommandMatcher.default(Path(temp))
            except CommandFileError as e:
                return {"ok": False, "problem": _readable(str(e), temp)}
            os.replace(temp, self._path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)
        return {"ok": True, "applies": "restart"}


def key_text(key: KeyInput) -> str:
    """A key as the player knows it: "Right Alt + O", "Numpad 8", "Mouse 4"."""
    parts = [_MODIFIERS.get(m, m.capitalize()) for m in key.modifiers]
    parts.append(_key_name(key.key))
    return " + ".join(parts)


_MODIFIERS = {
    "lalt": "Left Alt",
    "ralt": "Right Alt",
    "lctrl": "Left Ctrl",
    "rctrl": "Right Ctrl",
    "lshift": "Left Shift",
    "rshift": "Right Shift",
}


def _key_name(key: str) -> str:
    if key in _MODIFIERS:
        return _MODIFIERS[key]
    if key.startswith("np_"):
        return "Numpad " + key[3:].replace("_", " ").title()
    if key.startswith("mouse") and key[5:].isdigit():
        return f"Mouse {key[5:]}"
    if len(key) == 1 or (key[0] == "f" and key[1:].isdigit()):
        return key.upper()  # "O", "5", "F5"
    return _NAMES.get(key, key.replace("_", " ").title())


_NAMES = {"esc": "Esc", "pgup": "Page Up", "pgdn": "Page Down", "ins": "Insert", "del": "Delete"}


def _label(command_id: str, actions: tuple[str, ...], keybinds: Keybinds | None) -> str:
    """What a command does, in the game's words, or its id made readable."""
    if keybinds is not None:
        for name in actions:
            action = keybinds.get(name)
            if action is not None and action.label.strip():
                return action.label.strip()
    return command_id.replace("_", " ").capitalize()


def _clean(phrase: object) -> str:
    return " ".join(phrase.lower().split()) if isinstance(phrase, str) else ""


def _by_id(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for kind in ("command", "sequence"):
        for raw in data.get(kind, []):
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                entries[raw["id"]] = {**raw, "_kind": kind}
    return entries


def _phrases(entries: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    """The player's own phrases for each id: those not in the built-in entry it copies."""
    built_in = _built_in()
    out: dict[str, list[str]] = {}
    for entry_id, entry in entries.items():
        theirs = [str(p) for p in entry.get("say", [])]
        base = {str(p) for p in built_in.get(entry_id, {}).get("say", [])}
        out[entry_id] = [p for p in theirs if p not in base]
    return out


def _built_in() -> dict[str, dict[str, Any]]:
    text = resources.files("tycho.commands").joinpath("commands.toml").read_text("utf-8")
    return _by_id(tomllib.loads(text))


def _is_plain_copy(entry: Table, built_in: Mapping[str, Any]) -> bool:
    """The entry is the built-in, unchanged but for phrases, and has none of its own."""
    mine = {k: v for k, v in entry.unwrap().items()}
    theirs = {k: v for k, v in built_in.items() if k != "_kind"}
    same_but_say = {k: v for k, v in mine.items() if k != "say"} == {
        k: v for k, v in theirs.items() if k != "say"
    }
    return same_but_say and set(mine.get("say", [])) <= set(theirs.get("say", []))


def _drop(doc: Any, command_id: str) -> None:
    for kind in ("command", "sequence"):
        tables = doc.get(kind)
        if isinstance(tables, AoT):
            kept = [t for t in tables if t.get("id") != command_id]
            if len(kept) != len(tables):
                if kept:
                    new = tomlkit.aot()
                    for table in kept:
                        new.append(table)
                    doc[kind] = new
                else:
                    del doc[kind]


def _readable(error: str, temp: str) -> str:
    """The loader's message, about the player's file rather than the scratch copy."""
    message = error.replace(temp, "your commands").split(": ", 1)[-1]
    if "is in both" in message:
        return f"That phrase belongs to another of your commands ({message.split(' and ')[-1]})."
    return message


def _plain(row: Row) -> dict[str, object]:
    return {
        "id": row.id,
        "label": row.label,
        "said": row.said,
        "mine": row.mine,
        "key": row.key,
        "toggle": row.toggle,
        "to_bind": row.to_bind,
        "confirm": row.confirm,
        "sequence": row.sequence,
        "steps": row.steps,
    }
