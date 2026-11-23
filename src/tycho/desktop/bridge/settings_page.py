"""The Settings page and the keys (ADR 17): every setting with its rules, changing one
as voice does, undo, and storing a key (which never comes back out)."""

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from tycho import crash_reports, credentials, settings
from tycho.desktop.bridge.state import (
    DEVICES_SECONDS,
    SETUP_CHOICES,
    UNDO_DEPTH,
    State,
    plain_error,
    setting_choices,
    setting_label,
)
from tycho.games import hangar_hint
from tycho.packs import GameDataError, PackError, built_in, installed
from tycho.settings import Settings, SettingsError
from tycho.tile.design import TYCHO_ACCENT

log = logging.getLogger(__name__)

GROUPS: dict[str, tuple[str, ...]] = {
    "Voice": (
        "voice",
        "voice_volume",
        "feedback_volume",
        "call_me",
        "call_me_says",
        "call_me_how_often",
        "callouts",
    ),
    "Game and controls": ("game", "push_to_talk", "microphone"),
    "Safety": ("key_actions", "confirm_seconds"),
    "Privacy": (
        "keep_transcripts",
        "web_search",
        "web_search_open",
        "check_for_updates",
        "crash_reports",
        "look_at_screen",
    ),
    "The Tile": tuple(n for n in Settings.model_fields if n == "tile" or n.startswith("tile_")),
    "Advanced": (
        "brain_provider",
        "brain_model",
        "brain_seconds",
        "voice_model",
        "game_channel",
        "game_root",
        "transcription_device",
    ),
}
"""Which settings each tab shows (The Tile has a place of its own; Advanced holds what most
players never change). Every setting but ``version`` is in one (a test checks)."""
KEYS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "elevenlabs": "ElevenLabs",
}
RESTARTS = frozenset({"game"})
"""Settings a change in the app applies by restarting the voice loop (#295): the game's
keybinds, briefing and look-ups are all loaded at start (ADR 18)."""


class SettingsPage(State):
    def settings(self) -> dict[str, object]:
        """Every setting the app shows: its group, kind, value, limits, choices and rules."""
        current = settings.load(self._path)
        schema = Settings.model_json_schema()
        props: dict[str, Any] = schema.get("properties", {})
        groups: list[dict[str, object]] = []
        hidden = _not_for(current.game)
        for group, names in GROUPS.items():
            rows = [
                self._row(name, _value(current, name), props.get(name, {}))
                for name in names
                if name not in hidden
            ]
            for row in rows:
                if row["name"] == "tile_hidden_cards":  # a switch for each kind (#422)
                    row["kind"], row["cards"] = "cards", card_kinds(current.game)
                if row["name"] == "callouts":  # a switch for each kind, with its line (#4)
                    row["kind"], row["callouts"] = "callouts", callout_kinds(current.game)
                if row["name"] == "tile_screen":  # the screens plugged in now (#447)
                    row["kind"], row["screens"] = "screen", self._screens(current.tile_screen)
            groups.append({"name": group, "rows": rows})
        return {
            "groups": groups,
            "can_undo": bool(self._undo),
            "hangar": hangar_hint(built_in(current.game)),
        }

    def _row(self, name: str, value: object, prop: Mapping[str, Any]) -> dict[str, object]:
        field = Settings.model_fields[name]
        rule = settings.rule(name)
        row: dict[str, object] = {
            "name": name,
            "label": setting_label(name),
            "description": field.description or "",
            "value": _shown(name, value),
            "voice": rule["voice"],
            "applies": rule["applies"],
            "how": rule.get("how", ""),
            "kind": _kind(name, field.annotation, prop),
        }
        if choices := setting_choices(field.annotation):
            row["choices"] = list(choices)
        if name == "game":
            row["packs"] = shown_packs()
        for key in ("minimum", "maximum"):
            if key in prop:
                row[key] = prop[key]
        return row

    def _screens(self, chosen: str) -> list[dict[str, str]]:
        """Follow the game, then each screen; a chosen one that isn't plugged in is
        still listed, as itself, so it isn't lost by opening the page (#447)."""
        now = int(time.monotonic() // DEVICES_SECONDS)  # a screen plugged in shows soon
        found = cast(list[dict[str, str]], self._cached("screens", now, self._pc.screens))
        shown = [{"value": "game", "name": "Follow the game"}, *found]
        if all(s["value"] != chosen for s in shown):
            shown.append({"value": chosen, "name": f"{chosen} (not plugged in now)"})
        return shown

    def keys(self) -> dict[str, dict[str, object]]:
        """Which keys are stored, and their last four characters: never the key."""
        out: dict[str, dict[str, object]] = {}
        for name, label in KEYS.items():
            try:
                stored = credentials.get(name)
            except credentials.CredentialStoreError as e:
                out[name] = {"label": label, "stored": False, "ends": "", "problem": str(e)}
                continue
            if stored and not credentials.usable(stored):
                # Tycho ignores it (#248), so the page mustn't say it's in use, nor show
                # an ending with the invisible character in it (#250).
                out[name] = {
                    "label": label,
                    "stored": True,
                    "unusable": True,
                    "ends": "",
                    "problem": (
                        "This key has a character keys don't have, maybe an invisible "
                        "one, so Tycho can't use it. Remove it, then paste it again."
                    ),
                }
                continue
            out[name] = {
                "label": label,
                "stored": bool(stored),
                "ends": stored[-4:] if stored else "",
            }
        return out

    def set(self, name: object, value: object) -> dict[str, object]:
        """Change one setting. Answers what changed and when it applies, or the problem."""
        if not isinstance(name, str) or name not in Settings.model_fields or name == "version":
            return {"ok": False, "problem": f"{name!r} isn't a setting"}
        if name in SETUP_CHOICES:
            return {"ok": False, "problem": f"{setting_label(name)} is chosen with its own control"}
        before = getattr(settings.load(self._path), name)
        try:
            after = getattr(settings.update({name: value}, self._path), name)
        except SettingsError as e:
            return {"ok": False, "problem": plain_error(str(e))}
        if name == "crash_reports" and after is not True:
            crash_reports.forget()  # the player said no: nothing waiting is sent (ADR 4)
        if after == before:
            return {"ok": True, "changed": False}
        self._undo = [*self._undo, (name, before)][-UNDO_DEPTH:]
        return self._applied(name, after)

    def undo(self) -> dict[str, object]:
        """Put back the last change made in the app."""
        if not self._undo:
            return {"ok": False, "problem": "nothing to undo"}
        name, before = self._undo.pop()
        try:
            after = getattr(settings.update({name: before}, self._path), name)
        except SettingsError as e:
            return {"ok": False, "problem": plain_error(str(e))}
        return self._applied(name, after)

    def _applied(self, name: str, value: object) -> dict[str, object]:
        applies: str = settings.rule(name)["applies"]
        if name in RESTARTS:
            try:
                if self._restart():
                    applies = "restarting"  # the voice loop starts again with it
            except Exception:
                log.exception("Couldn't restart Tycho for %s", name)
        elif applies == "now":
            try:
                if not self._apply(name, value):
                    applies = "restart"  # nothing running to apply it to
            except Exception:
                log.exception("Couldn't apply %s in the running Tycho", name)
                applies = "restart"
        return {
            "ok": True,
            "changed": True,
            "name": name,
            "label": setting_label(name),  # the setup view has no settings list to look it up in
            "value": value,
            "applies": applies,
        }

    def choose_hangar_file(self) -> dict[str, object]:
        """Pick a hangar export and see what importing it would change (#556). Nothing
        is saved until ``save_hangar``."""
        self._hangar = None
        file = self._choose_file()
        if not file:
            return {"ok": False, "cancelled": True}
        current = settings.load(self._path)
        try:
            prepared = self._import_hangar(built_in(current.game), Path(file))
        except (ValueError, OSError, GameDataError) as e:
            return {"ok": False, "problem": str(e)}
        except Exception as e:  # a bad file mustn't leave the button doing nothing
            log.exception("Couldn't read the hangar export")
            return {"ok": False, "problem": f"Tycho couldn't read that file: {e}"}
        if prepared.changes:
            self._hangar = prepared
        return {"ok": True, "lines": prepared.lines, "changes": prepared.changes}

    def save_hangar(self) -> dict[str, object]:
        """Save the hangar import the player was just shown."""
        prepared, self._hangar = self._hangar, None
        if prepared is None:
            return {
                "ok": False,
                "problem": "Nothing is waiting to be saved. Choose the file again.",
            }
        try:
            prepared.save()
        except OSError as e:
            return {"ok": False, "problem": f"Couldn't save your fleet: {e}"}
        return {"ok": True, "done": prepared.done}

    def store_key(self, name: object, key: object) -> dict[str, object]:
        """Check a key with its provider, then store it in Windows Credential Manager."""
        if not isinstance(name, str) or name not in KEYS:
            return {"ok": False, "problem": "that isn't a key Tycho uses"}
        key = key.strip() if isinstance(key, str) else ""
        if problem := _shape(name, key):
            return {"ok": False, "problem": problem}
        if self._key_check is not None and (problem := self._key_check.check(name, key)):
            return {"ok": False, "problem": problem}
        try:
            credentials.set_secret(name, key)
        except Exception as e:
            return {
                "ok": False,
                "problem": f"Windows Credential Manager refused it ({type(e).__name__})",
            }
        log.info("%s key stored from the app", KEYS[name])
        # The brain and the voice are made when Tycho starts: reloading them is next (#12).
        return {"ok": True, "ends": key[-4:], "applies": "restart"}

    def remove_key(self, name: object) -> dict[str, object]:
        if not isinstance(name, str) or name not in KEYS:
            return {"ok": False, "problem": "that isn't a key Tycho uses"}
        try:
            removed = credentials.delete(name)
        except credentials.CredentialStoreError as e:
            return {"ok": False, "problem": str(e)}
        return {"ok": True, "removed": removed, "applies": "restart"}


def _shown(name: str, value: object) -> object:
    """A value as the page shows it: push-to-talk by its name ("Mouse side (forward)")."""
    if name == "push_to_talk" and isinstance(value, str) and value:
        from tycho.ptt.binding import Binding
        from tycho.ptt.devices import describe

        try:
            return describe(Binding.parse(value))
        except ValueError:
            return value
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


FOLDER_SETTINGS = frozenset({"game_channel", "game_root"})
"""Where a game is installed: shown only for a game Tycho reads something from."""


def card_kinds(game: str) -> list[dict[str, str]]:
    """The kinds of card the Tile can show in ``game``, each with its name: the ones
    every game has, and the game's own from its pack (#422)."""
    from tycho.tile.cards import ALL_KINDS, NAMES

    try:
        own = built_in(game).tile.cards
    except PackError:
        own = {}
    names = {**NAMES, **own}
    return [{"kind": k, "name": names[k]} for k in ALL_KINDS if k in names]


def callout_kinds(game: str) -> list[dict[str, str]]:
    """The kinds of event ``game`` can tell the player about unasked, each with its name
    and a line as it's said (#4). None for a game whose pack offers none."""
    try:
        offered = built_in(game).callouts
    except PackError:
        offered = ()
    return [{"kind": c.id, "name": c.name, "example": c.example} for c in offered]


def _not_for(game: str) -> frozenset[str]:
    """The settings that mean nothing in the chosen game, so its Settings leave them out:
    a game whose pack reads nothing from its folder has no folder to choose, and one
    with no window to look at has no looking (#31)."""
    try:
        pack = built_in(game)
    except PackError:  # tycho check and Status say why; show everything
        return frozenset()
    hidden = FOLDER_SETTINGS if pack.parts.install is None else frozenset[str]()
    return hidden if pack.look else hidden | {"look_at_screen"}  # it can't look (#31)


def shown_packs() -> list[dict[str, str]]:
    """Each game pack Tycho ships, for the Game setting: its name, and whether Tycho may
    press keys there, and why not, in the pack's words (#295)."""
    shown: list[dict[str, str]] = []
    for pack_id in installed():
        try:
            pack = built_in(pack_id)
        except PackError:
            log.exception("Game pack %s didn't load", pack_id)
            continue
        shown.append(
            {
                "id": pack.id,
                "name": pack.name,
                "mode": pack.mode,
                "why": pack.why,
                "accent": pack.accent or TYCHO_ACCENT,
            }
        )
    return shown


def _value(current: Settings, name: str) -> object:
    """A setting's value as the page shows it: ``call_me`` as the game uses it (#380)."""
    if name == "call_me":
        from tycho.address import called

        return called(current)
    return getattr(current, name)


def _kind(name: str, annotation: object, prop: Mapping[str, Any]) -> str:
    if name in SETUP_CHOICES:
        return "setup"
    if annotation is bool or annotation == bool | None:  # None: not asked yet
        return "switch"
    if annotation is float:
        return "percent" if prop.get("maximum", 2) <= 1 else "number"
    if setting_choices(annotation):
        return "choice"
    return "text"


def _shape(name: str, key: str) -> str:
    if name == "anthropic" and not key.startswith("sk-ant-"):
        return "That doesn't look like an Anthropic key: it starts with sk-ant-."
    if name == "openrouter" and not key.startswith("sk-or-"):
        return "That doesn't look like an OpenRouter key: it starts with sk-or-."
    if name == "elevenlabs" and not key.startswith("sk_"):
        return "That doesn't look like an ElevenLabs key: it starts with sk_."
    if len(key) < 24 or any(c.isspace() for c in key):
        return "That key looks cut short. Copy it again, all of it."
    if not credentials.usable(key):
        return "That key has a character keys don't have, maybe an invisible one. Copy it again."
    return ""
