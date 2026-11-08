"""The app's Commands page (ADR 17): what the player can say, and their own phrases.

The list joins the built-in commands, the player's commands.toml and their keybinds:
each command's phrases (the built-in ones and the player's), the key it presses, and
for one with no key, the game action to bind.

Adding a phrase to one of the player's own commands adds it to that entry's ``say``.
Adding one to a built-in command writes a ``[[phrases]]`` entry for it instead, which
only adds phrases: the built-in isn't copied, so later releases' changes to it still
reach the player (#134, and the tycho-commands skill). An emptied ``[[phrases]]`` entry
is left in the file, since removing a table can take the comments below it. The file is
edited with tomlkit, which keeps the player's own entries and comments as they are, and
a change is written only after the new file loads as Tycho would load it. Changes apply
when Tycho next starts.
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

from tycho.commands import Command, CommandFileError, CommandMatcher, resolve
from tycho.commands.reserved import USES, reserved_for
from tycho.game.keybinds import Keybinds, KeyInput
from tycho.safety import is_confirm_grade
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
        mine = {} if problem else _phrases(self._user_data())  # its phrases aren't in use
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
                        confirm=_asks(command, keybinds),
                        sequence=True,
                        steps=steps,
                    )
                )
                continue
            label = _label(command.id, command.actions, keybinds)
            if keybinds is None:
                rows.append(Row(command.id, label, said, yours, confirm=_asks(command, None)))
                continue
            found = resolve(command, keybinds)
            if found.action is None:
                rows.append(
                    Row(
                        command.id,
                        label,
                        said,
                        yours,
                        to_bind=label,
                        confirm=_asks(command, keybinds),
                    )
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
                    confirm=_asks(command, keybinds),
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

    def _user_data(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return tomllib.loads(self._path.read_text("utf-8-sig"))
        except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
            return {}

    # --- changing --------------------------------------------------------------------

    def add_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """Give a command one more phrase, the player's own."""
        said = _clean(phrase)
        if not said:
            return {"ok": False, "problem": "Type the words you'll say."}
        if len(said) > MAX_PHRASE:
            return {"ok": False, "problem": f"Keep it under {MAX_PHRASE} characters."}
        if (use := reserved_for(said)) is not None:  # the session answers it first (#227)
            return {
                "ok": False,
                "problem": f"“{said}” is one of Tycho's own words, for {USES[use]}, so the "
                "command would never hear it. Choose other words.",
            }
        if mistake := self._mistake():
            return {"ok": False, "problem": mistake}
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
        have = [str(p) for p in say] + list(self._said(str(command_id)))
        if _key(said) in {_key(p) for p in have}:
            return {"ok": False, "problem": f"“{said}” is already one of its phrases."}
        say.append(said)
        took = self._owner(said, command_id)
        result = self._write(doc, command_id)
        if result["ok"] and took:
            result["took_from"] = took
        return result

    def remove_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """Take away one of the player's own phrases."""
        said = _clean(phrase)
        try:
            doc, _ = self._entry(command_id, create=False)
        except (OSError, UnicodeDecodeError, tomlkit.exceptions.TOMLKitError) as e:
            return {
                "ok": False,
                "problem": f"Your commands file has a mistake; fix it first ({e}).",
            }
        if said not in _phrases(self._user_data()).get(str(command_id), []):
            return {"ok": False, "problem": "That isn't one of your phrases."}  # a built-in one
        for table in _tables(doc, str(command_id)):  # their own entry, then its [[phrases]]
            say = table.get("say")
            if isinstance(say, Array) and said in [str(p) for p in say]:
                del say[[str(p) for p in say].index(said)]  # tomlkit arrays keep comments
                return self._write(doc, command_id)
        return {"ok": False, "problem": "That isn't one of your phrases."}

    def _entry(self, command_id: object, *, create: bool) -> tuple[Any, Table | None]:
        """The player's file as a document, and where its phrases for ``command_id``
        go: their own entry with that id, or else its ``[[phrases]]`` entry; with
        ``create``, a new ``[[phrases]]`` entry for a built-in command."""
        text = self._path.read_text("utf-8-sig") if self._path.exists() else NEW_FILE
        doc = tomlkit.parse(text)
        if not isinstance(command_id, str):
            return doc, None
        for table in _tables(doc, command_id):
            return doc, table
        tables = doc.get("phrases")
        if command_id not in _built_in() or not create:
            return doc, None
        table = tomlkit.table()
        table.comment("more ways to say a built-in command (Tycho's window)")
        table["command"] = command_id
        table["say"] = tomlkit.array()
        if not isinstance(tables, AoT):
            doc["phrases"] = tomlkit.aot()
            tables = doc["phrases"]
        cast(AoT, tables).append(table)
        return doc, cast(AoT, tables)[-1]

    def _mistake(self) -> str:
        """Why the file doesn't load as it is, if it doesn't. Adding to it then could
        overwrite what the player meant (a ``[phrases]`` table with one bracket pair),
        or be refused for a clash that isn't the new phrase's."""
        try:
            CommandMatcher.default(self._path)
        except CommandFileError as e:
            return f"Your commands file has a mistake; fix it first ({str(e).split(': ', 1)[-1]})."
        return ""

    def _said(self, command_id: str) -> tuple[str, ...]:
        """Every phrase the command has now, built-in and the player's."""
        try:
            command = CommandMatcher.default(self._path).get(command_id)
        except CommandFileError:
            return ()
        return command.say if command is not None else ()

    def _owner(self, said: str, command_id: object) -> str:
        """The built-in command that had this phrase, which it now leaves (ADR 14)."""
        words = tuple(normalize(said))
        for entry_id, entry in _built_in().items():
            if entry_id != command_id and any(
                tuple(normalize(str(p))) == words for p in entry.get("say", [])
            ):
                return entry_id
        return ""

    def _write(self, doc: Any, command_id: object) -> dict[str, object]:
        """Write the file if Tycho can load it; otherwise say why and change nothing.
        ``command_id`` is the command changed, so a refusal can name the other one."""
        text = tomlkit.dumps(doc)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".commands-", suffix=".toml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            try:
                CommandMatcher.default(Path(temp))
            except CommandFileError as e:
                return {"ok": False, "problem": _readable(str(e), temp, str(command_id))}
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
        for raw in _list(data.get(kind)):
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                entries[raw["id"]] = {**raw, "_kind": kind}
    return entries


def _phrases(data: Mapping[str, Any]) -> dict[str, list[str]]:
    """The player's own phrases for each id: their ``[[phrases]]``, and those of their
    entries not in the built-in entry with the same id (an older copy of it)."""
    built_in = _built_in()
    out: dict[str, list[str]] = {}
    for entry_id, entry in _by_id(data).items():
        theirs = [str(p) for p in _list(entry.get("say"))]
        base = {str(p) for p in built_in.get(entry_id, {}).get("say", [])}
        out[entry_id] = [p for p in theirs if p not in base]
    for raw in _list(data.get("phrases")):
        if isinstance(raw, dict) and isinstance(raw.get("command"), str):
            mine = out.setdefault(raw["command"], [])
            mine += [str(p) for p in _list(raw.get("say")) if str(p) not in mine]
    return out


def _list(value: object) -> list[Any]:
    """A list from a hand-written file, or nothing: the loader reports the mistake."""
    return cast(list[Any], value) if isinstance(value, list) else []


def _key(phrase: str) -> tuple[str, ...]:
    return tuple(normalize(phrase))


def _asks(command: Command, keybinds: Keybinds | None) -> bool:
    """Whether Tycho asks first, as the safety gate decides it: the command says so,
    or something it presses always asks. That's its ``setup`` actions and the action
    it resolves to (any of its actions, without keybinds). A sequence asks if any step
    does."""
    if command.confirm:
        return True
    if command.is_sequence:
        return any(_asks(s.command, keybinds) for s in command.steps)
    pressed: tuple[str, ...] = command.actions
    if keybinds is not None:
        found = resolve(command, keybinds).action
        pressed = (found.name,) if found is not None else command.actions
    return any(is_confirm_grade(a) for a in (*command.setup, *pressed))


def _tables(doc: Any, command_id: str) -> list[Table]:
    """The player's own entry for ``command_id``, then its ``[[phrases]]`` entry."""
    found: list[Table] = []
    for kind, key in (("command", "id"), ("sequence", "id"), ("phrases", "command")):
        tables = doc.get(kind)
        if isinstance(tables, AoT):
            found += [t for t in tables if t.get(key) == command_id]
    return found


def _built_in() -> dict[str, dict[str, Any]]:
    text = resources.files("tycho.commands").joinpath("commands.toml").read_text("utf-8")
    return _by_id(tomllib.loads(text))


def _readable(error: str, temp: str, editing: str = "") -> str:
    """The loader's message, about the player's file rather than the scratch copy."""
    message = error.replace(temp, "your commands").split(": ", 1)[-1]
    if "is in both" in message:
        both = message.split(" is in both ", 1)[-1].split(" and ")
        other = next((i for i in both if i != editing), both[-1])
        return f"That phrase belongs to another of your commands ({other})."
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
