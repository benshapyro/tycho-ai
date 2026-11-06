"""Settings by voice (ADR 5, #111): "turn your voice down", "stop keeping transcripts".

The brain calls the ``settings`` tool, a look-up like ``update_fleet``: it reads or
changes ``settings.toml`` and tells Claude what happened, and Claude says it. It never
presses a key. Every rule comes from the schema (``settings.rule``):

- ``yes``: changeable by voice either way.
- ``lower_only``: only lower, or off, by voice. Raising it, or switching it on, needs
  the file, so a misheard sentence never weakens a safeguard or the player's privacy.
- ``no``: never by voice (the push-to-talk button, the microphone, paths); the tool
  says how instead.

A change is saved at once, with everything else in the file left as it is. Settings
that apply ``now`` change the running session through ``live``; the rest apply at the
next start, and the result says so. ``undo`` puts back the last change made by voice,
whatever its rule, since it only restores what the player had before, and only for
a few minutes: a later "undo" more likely means something else.
"""

import asyncio
import json
import logging
import math
import threading
import time
import typing
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import annotated_types
from pydantic import ValidationError

from tycho import settings
from tycho.brain.lookups import LookupResult, failure
from tycho.settings import Settings, SettingsError

log = logging.getLogger(__name__)

UNDO_DEPTH = 10
"""How many voice changes "undo" can walk back, newest first."""
UNDO_SECONDS = 180.0
"""How long after a change "undo" still means it. Later, the player most likely means
something else (the last key press), so an old change stays as it is."""

type Live = MutableMapping[str, Callable[[object], None]]
"""What applies a ``now`` setting to the running session, by setting name. Filled in as
the parts it changes are built; a name missing from it applies at the next start."""

_TRUE = {"on", "true", "yes", "1", "enable", "enabled", "show", "shown", "visible"}
_FALSE = {"off", "false", "no", "0", "disable", "disabled", "none", "hide", "hidden", "invisible"}
"""Hide, hidden and invisible are for "hide the Tile" (eval, 2026-11-05)."""
NO_NAME = frozenset({"nothing", "none", "nobody", "no name", "off", "no", "empty", ""})
"""What clears a text setting, such as what Tycho calls the player."""
ORDERED = frozenset({"tile_size", "tile_cards_stay"})
"""Choices in order, so "bigger" is one step up: "make the Tile bigger" (eval, 2026-11-05)."""
_STEPS = {
    **dict.fromkeys(("bigger", "larger", "up", "more", "longer"), 1),
    **dict.fromkeys(("smaller", "down", "less", "shorter"), -1),
}

DESCRIPTION = """\
Read or change Tycho's own settings, when the player asks about or for a change to \
how Tycho works: its voice and sound volumes, how long they have to say "confirm", \
web search, keeping transcripts, update checks, game channel, speech recognition \
device. action "get" reads them all, with which can change by voice. action "set" \
changes the ones named; a value can be relative ("-10%" for a bit quieter, "+2" \
seconds). action "undo" puts back the last change made by voice. Some settings only \
go down or off by voice: the result says so, and how to change them instead. Never by \
voice, so don't call this for them: the push-to-talk button (the player runs \
`tycho bind` and presses the new button), the microphone and the game folder (in \
settings.toml). "Sounds" may mean the beeps or the voice too: change what was asked. \
Never change a setting the player didn't ask about."""


@dataclass(frozen=True, slots=True)
class Parsed:
    value: object
    note: str = ""
    """Said back to the player, e.g. that the value was brought into range."""


class Refused(Exception):
    """A change that can't be made by voice, with why and what to do instead."""


def voice_names() -> tuple[str, ...]:
    """Settings that can change by voice in at least one direction."""
    return tuple(n for n in Settings.model_fields if settings.rule(n)["voice"] != "no")


def parse(name: str, said: str, current: object) -> Parsed:
    """What the player asked for, as the setting's own type. Raises ``Refused``."""
    field = Settings.model_fields.get(name)
    if field is None:
        raise Refused(f"{name} isn't a setting")
    text = said.strip().lower()
    kind = field.annotation
    if kind is bool:
        if text in _TRUE:
            return Parsed(True)
        if text in _FALSE:
            return Parsed(False)
        raise Refused(f"{name} is on or off, not {said!r}")
    if kind is float:
        return _number(name, text, current, field.metadata)
    if kind is str:
        value = "" if text in NO_NAME else said.strip()
        try:
            Settings.model_validate({name: value})
        except ValidationError as e:
            raise Refused(f"{name}: {e.errors()[0]['msg']}") from e
        return Parsed(" ".join(value.split()))
    options = typing.get_args(getattr(kind, "__value__", kind))
    if options and all(isinstance(o, str) for o in options):
        choices = cast(tuple[str, ...], options)
        for option in choices:
            if text == option.lower():
                return Parsed(option)
        # Claude says "+20%" for bigger, as it would for a volume (eval, 2026-11-05).
        step = _STEPS.get(text) or {"+": 1, "-": -1}.get(text[:1], 0)
        if name in ORDERED and current in choices and step:
            at = choices.index(cast(str, current)) + step
            if 0 <= at < len(choices):
                return Parsed(choices[at])
            return Parsed(cast(str, current), f"{current} is already as far as it goes")
        raise Refused(f"{name} is one of {', '.join(options)}, not {said!r}")
    raise Refused(f"{name} can't be set by voice")


def _number(name: str, text: str, current: object, metadata: list[object]) -> Parsed:
    low = next((float(m.ge) for m in metadata if isinstance(m, annotated_types.Ge)), -math.inf)  # pyright: ignore[reportArgumentType]
    high = next((float(m.le) for m in metadata if isinstance(m, annotated_types.Le)), math.inf)  # pyright: ignore[reportArgumentType]
    share = high <= 1.0  # a 0-1 setting: the player says percent
    if text in ("max", "maximum", "full", "loudest", "longest"):
        return Parsed(high)
    if text in ("min", "minimum", "off", "mute", "zero", "shortest"):
        return Parsed(low)
    relative = text[:1] in "+-"
    raw = text.removesuffix("%").removesuffix("percent").removesuffix("seconds").strip()
    raw = raw.removesuffix("s").strip()
    try:
        number = float(raw)
    except ValueError as e:
        raise Refused(f"{name} needs a number, not {text!r}") from e
    if share and (text.endswith(("%", "percent")) or abs(number) > 1):
        number /= 100
    if relative:
        number += float(cast(float, current))
    clamped = min(high, max(low, number))
    note = ""
    if clamped != number:
        shown = f"{clamped:.0%}" if share else f"{clamped:g}"
        note = f"the nearest allowed is {shown}"
    return Parsed(round(clamped, 2), note)


def check_rule(name: str, before: object, after: object) -> None:
    """Raises ``Refused`` if the change isn't allowed by voice."""
    rule = settings.rule(name)
    how = rule.get("how", "")
    if rule["voice"] == "no":
        raise Refused(f"{name} doesn't change by voice: {how}")
    if rule["voice"] == "lower_only" and _raises(before, after):
        word = "on" if isinstance(after, bool) else "up"
        raise Refused(f"{name} only goes down or off by voice; to turn it {word}, change it {how}")


def _raises(before: object, after: object) -> bool:
    if isinstance(before, bool) and isinstance(after, bool):
        return after and not before
    if isinstance(before, int | float) and isinstance(after, int | float):
        return after > before
    return False


def shown(name: str, value: object) -> object:
    """A value as the player hears it: volumes as percent."""
    field = Settings.model_fields[name]
    if field.annotation is float and isinstance(value, float):
        high = next((m.le for m in field.metadata if isinstance(m, annotated_types.Le)), None)
        if high == 1.0:
            return f"{value:.0%}"
    return value


class SettingsTool:
    """The ``settings`` tool. Satisfies ``brain.lookups.Lookup``."""

    instant = True
    """It answers at once, so Tycho doesn't say "Checking." first."""

    def __init__(
        self,
        path: Path | None = None,
        live: Live | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = path
        self._live: Live = live if live is not None else {}
        self._clock = clock
        self._undo: list[tuple[float, dict[str, object]]] = []
        """(when, the values before) for each change by voice, oldest first."""
        self._changing = threading.Lock()

    @property
    def name(self) -> str:
        return "settings"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set", "undo"]},
                "changes": {
                    "type": "array",
                    "description": 'For "set": the settings to change. Empty otherwise.',
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": list(voice_names())},
                            "value": {
                                "type": "string",
                                "description": 'e.g. "40%", "-10%", "3", "off", "PTU"',
                            },
                        },
                        "required": ["name", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["action", "changes"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        # Off the event loop: reading and writing the file mustn't hold up push-to-talk.
        return await asyncio.to_thread(self._locked, arguments)

    def _locked(self, arguments: Mapping[str, object]) -> LookupResult:
        with self._changing:
            try:
                return self._apply(arguments)
            except SettingsError as e:
                return failure(f"settings.toml has a problem, so nothing changed: {e}")

    def _apply(self, arguments: Mapping[str, object]) -> LookupResult:
        action = arguments.get("action")
        if action == "get":
            return _result({"settings": self._describe(settings.load(self._path))})
        if action == "undo":
            return self._undo_last()
        if action == "set":
            return self._set(_changes(arguments.get("changes")))
        return failure('action must be "get", "set" or "undo"')

    def _describe(self, current: Settings) -> dict[str, object]:
        out: dict[str, object] = {}
        for name, value in current.model_dump(mode="json").items():
            if name != "version":
                said = "not set" if value is None else shown(name, getattr(current, name))
                out[name] = {"value": said, "by_voice": settings.rule(name)["voice"]}
        return out

    def _set(self, asked: list[tuple[str, str]]) -> LookupResult:
        if not asked:
            return failure("name at least one setting to change")
        current = settings.load(self._path)
        updates: dict[str, object] = {}
        notes: dict[str, str] = {}
        problems: list[dict[str, str]] = []
        for name, said in asked:
            try:
                parsed = parse(name, said, getattr(current, name, None))
                check_rule(name, getattr(current, name), parsed.value)
            except Refused as e:
                problems.append({"name": name, "problem": str(e)})
                continue
            if parsed.value == getattr(current, name):
                problems.append(
                    {"name": name, "problem": f"it's already {shown(name, parsed.value)}"}
                )
                continue
            updates[name] = parsed.value
            if parsed.note:
                notes[name] = parsed.note
        result: dict[str, object] = {}
        if updates:
            before = {name: getattr(current, name) for name in updates}
            updated = settings.update(updates, self._path)
            self._undo = [*self._undo, (self._clock(), before)][-UNDO_DEPTH:]
            result["changed"] = self._applied(before, updated, notes)
            result["undo"] = 'say "undo" to put it back'
        if problems:
            result["problems"] = problems
        return _result(result)

    def _undo_last(self) -> LookupResult:
        fresh = self._clock() - UNDO_SECONDS
        self._undo = [entry for entry in self._undo if entry[0] >= fresh]
        if not self._undo:
            return failure(
                "there's no recent change by voice to undo; to change a setting, ask for it"
            )
        _, before = self._undo.pop()
        current = settings.load(self._path)
        now = {name: getattr(current, name) for name in before}
        updated = settings.update(before, self._path)
        return _result({"undone": self._applied(now, updated, {})})

    def _applied(
        self, before: Mapping[str, object], updated: Settings, notes: Mapping[str, str]
    ) -> list[dict[str, object]]:
        """Apply what can change now, log every change, and describe them for Claude."""
        out: list[dict[str, object]] = []
        for name, was in before.items():
            value = getattr(updated, name)
            applies = settings.rule(name)["applies"]
            change: dict[str, object] = {
                "name": name,
                "from": shown(name, was),
                "to": shown(name, value),
            }
            apply = self._live.get(name) if applies == "now" else None
            if apply is not None:
                try:
                    apply(value)
                    change["takes_effect"] = "now"
                except Exception:  # a failed live change still saved; it applies at restart
                    log.exception("Couldn't apply %s now", name)
                    change["takes_effect"] = "when Tycho restarts"
            else:
                change["takes_effect"] = "when Tycho restarts"
            if name in notes:
                change["note"] = notes[name]
            log.info("Setting changed by voice: %s %r -> %r", name, was, value)
            out.append(change)
        return out


def _changes(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[tuple[str, str]] = []
    for item in cast(list[object], value):
        if isinstance(item, dict):
            entry = cast(dict[str, object], item)
            name, said = entry.get("name"), entry.get("value")
            if isinstance(name, str) and isinstance(said, str | int | float | bool):
                out.append((name, str(said)))
    return out


def _result(data: Mapping[str, object]) -> LookupResult:
    return LookupResult(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
