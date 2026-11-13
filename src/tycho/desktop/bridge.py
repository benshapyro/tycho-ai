"""What the app window's page can ask of Tycho (ADR 17): the only way in, and narrow.

pywebview calls these methods from the page's JavaScript, each on a thread of its own,
with JSON values. Everything here checks what it's given, as the voice tool does, and
answers in plain words a page can show. pywebview exposes every public attribute of
this object to the page, so everything that isn't a call for the page starts with ``_``.

A change goes through ``settings.update`` and the same live callbacks as one made by
voice (``apply``), so it takes effect at once and lands in settings.toml. The app may
do what voice can't (a longer confirm time, transcripts on): its controls are the
player's own hands, not a sentence that might have been misheard.

Keys never come back out: the page sends one once, and learns only whether one is
stored and its last four characters.

Updates are checked, never installed: the page learns whether a newer Tycho is out, and
can ask for its release page to open in the browser. The page never names a web address.
"""

import base64
import dataclasses
import io
import logging
import threading
import time
import typing
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from tycho import __version__, credentials, paths, settings, updates
from tycho.desktop import setup
from tycho.desktop.commands import Commands
from tycho.desktop.keys import KeyCheck
from tycho.game.keybinds import Keybinds
from tycho.packs import GamePack, PackError, as_run, built_in, installed
from tycho.settings import Settings, SettingsError

log = logging.getLogger(__name__)

GROUPS: dict[str, tuple[str, ...]] = {
    "Voice and sounds": (
        "voice",
        "voice_model",
        "voice_volume",
        "feedback_volume",
        "call_me",
        "call_me_says",
        "call_me_how_often",
    ),
    "Brain": ("brain_provider", "brain_model", "brain_seconds"),
    "Safety": ("key_actions", "confirm_seconds"),
    "Privacy": ("keep_transcripts", "web_search", "web_search_open", "check_for_updates"),
    "Game and controls": (
        "game",
        "game_channel",
        "game_root",
        "push_to_talk",
        "microphone",
        "transcription_device",
    ),
    "The Tile": tuple(n for n in Settings.model_fields if n == "tile" or n.startswith("tile_")),
}
"""Which settings each tab shows. Every setting but ``version`` is in one (a test checks)."""
SETUP_CHOICES = frozenset({"game_root", "push_to_talk", "microphone"})
"""Chosen with their own controls (a folder, a button press, a device), from setup."""
KEYS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openrouter": "OpenRouter",
    "elevenlabs": "ElevenLabs",
}
RESTARTS = frozenset({"game"})
"""Settings a change in the app applies by restarting the voice loop (#295): the game's
keybinds, briefing and look-ups are all loaded at start (ADR 18)."""
UNDO_DEPTH = 20
DEVICES_SECONDS = 10.0
"""How long the list of microphones is kept between redraws."""
KEYBINDS_SECONDS = 30.0
"""How long the keybinds are kept for the Commands page."""
UPDATE_SECONDS = 3600.0
"""How long the window's own check for a newer Tycho is kept: each opening of the
window shouldn't ask GitHub again. The Check for updates button always asks."""
RELEASES_PAGE = f"https://github.com/{updates.REPO}/releases"


type Apply = Callable[[str, object], bool]
"""Applies a change in the running Tycho; False if it can't now (it applies at restart)."""
type Findings = Callable[[], Sequence[Mapping[str, str]]]


class Bridge:
    def __init__(
        self,
        *,
        apply: Apply = lambda _name, _value: False,
        findings: Findings = lambda: [],
        settings_path: Any = None,
        key_check: KeyCheck | None = None,
        ship_accent: Callable[[], str | None] = lambda: None,
        pc: setup.SetupPC | None = None,
        choose_folder: Callable[[], str | None] = lambda: None,
        finish: Callable[[], None] = lambda: None,
        first_run: bool = False,
        commands: Commands | None = None,
        latest_release: Callable[[], updates.Release | None] = updates.latest_release,
        open_page: Callable[[str], object] = webbrowser.open,
        restart: Callable[[], bool] = lambda: False,
    ) -> None:
        """``apply`` changes a setting in the running Tycho (the live callbacks);
        ``findings`` are the checks ``tycho check`` makes; ``key_check`` tries a key
        with its provider; ``ship_accent`` is the colour of the ship being flown.
        ``restart`` restarts the voice loop with the settings as saved (#295); False if
        nothing is running to restart. ``commands`` is for tests: otherwise the Commands
        page is the chosen game's."""
        self._apply = apply
        self._findings = findings
        self._path = settings_path
        self._key_check = key_check
        self._ship_accent = ship_accent
        self._pc = pc or setup.WindowsPC()
        self._cache: dict[str, tuple[object, object]] = {}
        """Slow look-ups (the game, its keybinds, the devices), kept while their inputs
        stay the same: each Next or redraw shouldn't search the disk again."""
        self._choose_folder = choose_folder
        self._finish = finish
        self._first_run = first_run
        self._given_commands = commands
        self._restart = restart
        self._capture = threading.Event()
        """Set to stop a push-to-talk capture the player has left."""
        self._undo: list[tuple[str, object]] = []
        self._latest_release = latest_release
        self._open_page = open_page
        self._checked: tuple[float, updates.Release | None] | None = None
        """When the window last asked GitHub, and what it said: kept for an hour (#376)."""
        self._release: updates.Release | None = None
        """The newer release the last check found: the only page ``open_release`` opens."""

    # --- reading ---------------------------------------------------------------------

    def about(self) -> dict[str, object]:
        """``first_run``: Tycho is waiting for setup before it starts listening."""
        return {"version": __version__, "first_run": self._first_run}

    def status(self) -> list[Mapping[str, str]]:
        """The same checks as ``tycho check``, quick ones only."""
        try:
            return list(self._findings())
        except Exception as e:  # a broken check must not break the page
            log.exception("The status checks failed")
            return [{"level": "fail", "what": "Checks", "detail": f"couldn't run: {e}", "fix": ""}]

    def check_updates(self, asked: object = True) -> dict[str, object]:
        """Is a newer Tycho out? ``asked`` is the player pressing Check for updates, so
        GitHub is asked even with ``check_for_updates`` off; the window's own check when
        it opens (``asked`` false) honours the setting, and is kept for an hour.

        ``state`` is ``newer``, ``latest``, ``none`` (no release yet), ``off`` or
        ``error``; Tycho never updates itself (#9)."""
        current = __version__
        if asked is not True:
            try:
                if not settings.load(self._path).check_for_updates:
                    return {"state": "off", "current": current}
            except SettingsError:
                return {"state": "off", "current": current}
        now = time.monotonic()
        try:
            if asked is not True and self._checked and now - self._checked[0] < UPDATE_SECONDS:
                release = self._checked[1]  # an hour since the last check, not the clock's
            else:
                release = self._latest_release()
                self._checked = (now, release)
        except updates.RateLimited as e:
            log.debug("GitHub wouldn't say if a newer Tycho is out: %s", e)
            return {
                "state": "error",
                "current": current,
                "problem": "GitHub didn't answer. Try again later.",
            }
        except updates.UpdateError as e:
            log.debug("Couldn't check for a newer Tycho: %s", e)
            return {
                "state": "error",
                "current": current,
                "problem": "Couldn't reach GitHub to check. Is the PC online? Try again later.",
            }
        if release is None:
            return {"state": "none", "current": current}
        if updates.is_newer(release.version, current):
            self._release = release
            return {"state": "newer", "current": current, "latest": release.version}
        return {"state": "latest", "current": current, "latest": release.version}

    def open_release(self) -> dict[str, object]:
        """Open the newer release's page (its installer is there) in the player's browser.
        Only a page of Tycho's own releases: anything else opens the list of releases."""
        found = self._release.url if self._release is not None else ""
        url = found if found.startswith(RELEASES_PAGE + "/") else RELEASES_PAGE
        try:
            self._open_page(url)
        except Exception as e:
            log.warning("Couldn't open the browser: %s", e)
            return {"ok": False, "problem": f"Couldn't open the browser. The page is {url}"}
        return {"ok": True, "url": url}

    def settings(self) -> dict[str, object]:
        """Every setting the app shows: its group, kind, value, limits, choices and rules."""
        current = settings.load(self._path)
        schema = Settings.model_json_schema()
        props: dict[str, Any] = schema.get("properties", {})
        groups: list[dict[str, object]] = []
        for group, names in GROUPS.items():
            rows = [self._row(name, _value(current, name), props.get(name, {})) for name in names]
            groups.append({"name": group, "rows": rows})
        return {"groups": groups, "can_undo": bool(self._undo)}

    def _row(self, name: str, value: object, prop: Mapping[str, Any]) -> dict[str, object]:
        field = Settings.model_fields[name]
        rule = settings.rule(name)
        row: dict[str, object] = {
            "name": name,
            "label": _label(name),
            "description": field.description or "",
            "value": _shown(name, value),
            "voice": rule["voice"],
            "applies": rule["applies"],
            "how": rule.get("how", ""),
            "kind": _kind(name, field.annotation, prop),
        }
        if choices := _choices(field.annotation):
            row["choices"] = list(choices)
        if name == "game":
            row["packs"] = _packs()
        for key in ("minimum", "maximum"):
            if key in prop:
                row[key] = prop[key]
        return row

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

    def tile_preview(self, width: int = 640) -> str:
        """The Tile as it is set now, sized and placed exactly as on a 2560 x 1440
        screen, drawn by the Tile itself and scaled to ``width``: a PNG data URL."""
        from PIL import Image

        from tycho.tile.board import Board
        from tycho.tile.cards import Card, Row
        from tycho.tile.display import accent, look, place
        from tycho.tile.draw import draw
        from tycho.tile.window import Screen

        current = settings.load(self._path)
        screen = Screen(0, 0, 2560, 1440)
        board = Board(clock=lambda: 0.0)
        board.show(
            Card(
                "list",
                title="Best places to sell",
                label="Sell · Laranite",
                source="UEX",
                rows=(
                    Row("Everus Harbor", "Hurston", "3,048"),
                    Row("Area18 TDD", "ArcCorp", "3,012"),
                    Row("Port Tressler", "microTech", "2,987"),
                ),
            )
        )
        try:
            ship = self._ship_accent()
        except Exception:
            ship = None
        image = _cockpit(screen.width, screen.height)
        if current.tile:
            tile_look = look(current, screen, accent(current.tile_colour, ship))
            light = "thinking" if current.tile_talk_light else None
            tile = draw(board.view(), tile_look, now=1.0, light=light)
            image.alpha_composite(tile, place(current, screen, tile.width, tile.height))
        width = max(320, min(int(width), screen.width))
        small = image.resize((width, width * 9 // 16), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        small.convert("RGB").save(buffer, "PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    # --- changing ----------------------------------------------------------------------

    def set(self, name: object, value: object) -> dict[str, object]:
        """Change one setting. Answers what changed and when it applies, or the problem."""
        if not isinstance(name, str) or name not in Settings.model_fields or name == "version":
            return {"ok": False, "problem": f"{name!r} isn't a setting"}
        if name in SETUP_CHOICES:
            return {"ok": False, "problem": f"{_label(name)} is chosen with its own control"}
        before = getattr(settings.load(self._path), name)
        try:
            after = getattr(settings.update({name: value}, self._path), name)
        except SettingsError as e:
            return {"ok": False, "problem": _plain(str(e))}
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
            return {"ok": False, "problem": _plain(str(e))}
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
            "label": _label(name),  # the setup view has no settings list to look it up in
            "value": value,
            "applies": applies,
        }

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

    # --- commands ----------------------------------------------------------------------

    def commands(self) -> dict[str, object]:
        """Every command and sequence: phrases, the player's own, keys, what to bind."""
        return self._commands().rows()

    def add_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """One more phrase for a command, in the player's commands.toml."""
        return self._commands().add_phrase(command_id, phrase)

    def remove_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """Take one of the player's own phrases away."""
        return self._commands().remove_phrase(command_id, phrase)

    def _commands(self) -> Commands:
        """The Commands page for the game the settings pick, and its own file (#295)."""
        if self._given_commands is not None:
            return self._given_commands
        pack = self._game_pack(settings.load(self._path))
        return Commands(paths.commands_file(pack.id), self._keybinds, pack)

    def _game_pack(self, current: Settings) -> GamePack:
        """The chosen game, as the player's ``key_actions`` runs it (#191)."""
        try:
            return as_run(built_in(current.game), key_actions=current.key_actions)
        except PackError:  # tycho check and the status page say why
            log.exception("The chosen game's pack didn't load")
            return as_run(built_in(), key_actions=current.key_actions)

    # --- setup: the choices made with their own controls -------------------------------

    def setup_state(self) -> dict[str, object]:
        """Everything setup shows: the game, push-to-talk, the microphone, the keys."""
        current = settings.load(self._path)
        game = self._game(current)
        ptt = current.push_to_talk
        return {
            "game": dataclasses.asdict(game),
            "channels": list(_choices(Settings.model_fields["game_channel"].annotation)),
            "push_to_talk": {
                "set": ptt is not None,
                "name": self._pc.name(ptt) if ptt else "",
                "conflicts": self._conflicts(ptt, current) if ptt else [],
            },
            "microphone": {"setting": current.microphone or "", "devices": self._devices()},
            "keys": self.keys(),
            "ready": ptt is not None and game.found,
        }

    def set_channel(self, channel: object) -> dict[str, object]:
        """Which version of the game: LIVE, PTU and so on."""
        result, current = self._save_choice("game_channel", channel)
        if current is not None:
            result["game"] = dataclasses.asdict(self._game(current))
        return result

    def choose_game_folder(self) -> dict[str, object]:
        """Pick the game's folder in Windows' own folder dialog."""
        folder = self._choose_folder()
        if not folder:
            return {"ok": False, "cancelled": True}
        channel = settings.load(self._path).game_channel
        root = self._pc.root(folder, channel)
        if root is None:
            return {"ok": False, "problem": f"There's no {channel}\\Data.p4k in or under {folder}."}
        result, current = self._save_choice("game_root", root)
        if current is not None:
            result["game"] = dataclasses.asdict(self._game(current))
        return result

    def capture_push_to_talk(self) -> dict[str, object]:
        """Wait for the player to press the button they want, and save it."""
        self._capture.set()  # one capture at a time: a new one replaces the last
        self._capture = cancel = threading.Event()
        try:
            found = self._pc.capture(setup.CAPTURE_SECONDS, cancel)
        except Exception as e:  # a joystick library that won't start, a device gone
            log.exception("Couldn't listen for a push-to-talk button")
            return {"ok": False, "problem": f"Tycho couldn't listen for a button: {e}"}
        if cancel.is_set():
            return {"ok": False, "cancelled": True}
        if found is None:
            seconds = round(setup.CAPTURE_SECONDS)
            return {"ok": False, "problem": f"No button was pressed in {seconds} seconds."}
        result, current = self._save_choice("push_to_talk", found)
        if current is not None:
            result["name"] = self._pc.name(found)
            result["conflicts"] = self._conflicts(found, current)
        return result

    def cancel_capture(self) -> dict[str, object]:
        """Stop waiting for a push-to-talk button: the player moved on. Otherwise the
        next key they press (Ctrl+V into a key box, Alt+Tab) would be saved."""
        self._capture.set()
        return {"ok": True}

    def set_microphone(self, name: object) -> dict[str, object]:
        """A microphone from the list, empty for Windows' default, or the one already
        saved (which may be a number, or unplugged now)."""
        if not isinstance(name, str):
            return {"ok": False, "problem": "That microphone isn't connected."}
        saved = settings.load(self._path).microphone or ""
        if name and name != saved and name not in self._devices(fresh=True):
            return {"ok": False, "problem": "That microphone isn't connected."}
        result, _ = self._save_choice("microphone", name or None)
        return result

    def microphone_level(self) -> dict[str, object]:
        """A short listen to the chosen microphone."""
        return self._pc.level(settings.load(self._path).microphone)

    def finish_setup(self) -> dict[str, object]:
        """Setup is done: on a first run, Tycho starts."""
        current = settings.load(self._path)
        missing = [
            what
            for what, ok in (
                ("the game", self._game(current).found),
                ("a push-to-talk button", current.push_to_talk is not None),
            )
            if not ok
        ]
        if missing:
            return {"ok": False, "problem": f"Tycho still needs {' and '.join(missing)}."}
        self._capture.set()
        self._finish()
        return {"ok": True, "first_run": self._first_run}

    def _save_choice(self, name: str, value: object) -> tuple[dict[str, object], Settings | None]:
        """A setup choice, saved; it applies when Tycho next starts. Also the settings
        as saved, so the caller needn't read the file again."""
        before = getattr(settings.load(self._path), name)
        try:
            current = settings.update({name: value}, self._path)
        except SettingsError as e:
            return {"ok": False, "problem": _plain(str(e))}, None
        if getattr(current, name) != before:
            self._undo = [*self._undo, (name, before)][-UNDO_DEPTH:]
        return {"ok": True, "applies": "start" if self._first_run else "restart"}, current

    # Slow look-ups, kept while their inputs are the same.

    def _game(self, current: Settings) -> setup.Game:
        key = (current.game, current.game_channel, str(current.game_root))
        return cast(setup.Game, self._cached("game", key, lambda: setup.game(current, self._pc)))

    def _conflicts(self, ptt: str, current: Settings) -> list[str]:
        key = (ptt, current.game, current.game_channel, str(current.game_root))
        found = self._cached("conflicts", key, lambda: self._pc.conflicts(ptt, current))
        return list(cast(list[str], found))

    def _keybinds(self) -> Keybinds | None:
        """The player's keybinds, for the Commands page: kept a while, since reading them
        parses the game's files, and they change only when the player rebinds in game."""
        current = settings.load(self._path)
        if self._game_pack(current).parts.keybinds is None:
            return None  # a game Tycho never presses keys in has none to read (#191)
        # The game too: once two packs read keybinds, a switch mustn't show the last
        # game's keys until the cache runs out (#331).
        key = (
            current.game,
            current.game_channel,
            str(current.game_root),
            time.monotonic() // KEYBINDS_SECONDS,
        )

        def read() -> Keybinds | None:
            from tycho.app import find_install
            from tycho.game.store import GameDataError, KeybindStore

            try:
                return KeybindStore.load(find_install(current)).keybinds
            except FileNotFoundError, GameDataError, OSError:
                return None

        return cast("Keybinds | None", self._cached("keybinds", key, read))

    def _devices(self, fresh: bool = False) -> list[str]:
        # Microphones come and go, so a new list each time setup is shown, not each click.
        key = time.monotonic() // DEVICES_SECONDS if not fresh else time.monotonic()
        return list(cast(list[str], self._cached("devices", key, self._pc.microphones)))

    def _cached(self, what: str, key: object, look: Callable[[], object]) -> object:
        hit = self._cache.get(what)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = look()
        self._cache[what] = (key, value)
        return value


def _shown(name: str, value: object) -> object:
    """A value as the page shows it: push-to-talk by its name ("Mouse side (forward)")."""
    if name == "push_to_talk" and isinstance(value, str) and value:
        from tycho.app import describe  # here: the app imports this module
        from tycho.ptt.binding import Binding

        try:
            return describe(Binding.parse(value))
        except ValueError:
            return value
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _packs() -> list[dict[str, str]]:
    """Each game pack Tycho ships, for the Game setting: its name, and whether Tycho may
    press keys there, and why not, in the pack's words (#295)."""
    shown: list[dict[str, str]] = []
    for pack_id in installed():
        try:
            pack = built_in(pack_id)
        except PackError:
            log.exception("Game pack %s didn't load", pack_id)
            continue
        shown.append({"id": pack.id, "name": pack.name, "mode": pack.mode, "why": pack.why})
    return shown


def _value(current: Settings, name: str) -> object:
    """A setting's value as the page shows it: ``call_me`` as the game uses it (#380)."""
    if name == "call_me":
        from tycho.address import called

        return called(current)
    return getattr(current, name)


def _label(name: str) -> str:
    special = {
        "tile": "Show the Tile",
        "brain_provider": "Who runs the brain",
        "brain_model": "Brain model",
        "brain_seconds": "Time to answer",
        "call_me": "What Tycho calls you",
        "call_me_says": "How to say it",
        "call_me_how_often": "How often",
        "confirm_seconds": "Time to say confirm",
        "feedback_volume": "Sound volume",
        "game": "Game",
        "game_root": "Game folder",
        "transcription_device": "Speech recognition on",
        "voice": "Voice",
        "voice_model": "Voice model",
    }
    if name in special:
        return special[name]
    return name.removeprefix("tile_").replace("_", " ").capitalize()


def _choices(annotation: object) -> tuple[str, ...]:
    options = typing.get_args(getattr(annotation, "__value__", annotation))
    return (
        cast(tuple[str, ...], options)
        if options and all(isinstance(o, str) for o in options)
        else ()
    )


def _kind(name: str, annotation: object, prop: Mapping[str, Any]) -> str:
    if name in SETUP_CHOICES:
        return "setup"
    if annotation is bool:
        return "switch"
    if annotation is float:
        return "percent" if prop.get("maximum", 2) <= 1 else "number"
    if _choices(annotation):
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


def _plain(error: str) -> str:
    """Pydantic's message, without its type names and links."""
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith(("1 validation", "For further")) and "errors.pydantic" not in line:
            if "[type=" in line:
                line = line.split("[type=", 1)[0].strip()
            if line and line not in Settings.model_fields:
                return line
    return "that value isn't allowed"


def _cockpit(width: int, height: int) -> Any:
    from PIL import Image, ImageDraw

    # Drawn on RGB so the see-through glare and ring blend (Pillow blends only onto RGB).
    image = Image.new("RGB", (width, height), (8, 12, 20))
    d = ImageDraw.Draw(image, "RGBA")
    for y in range(height):
        v = round(12 + 18 * y / height)
        d.line((0, y, width, y), fill=(v, v + 5, v + 14, 255))
    cx, cy, r = width // 2, round(height * 0.56), round(height * 0.24)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(245, 144, 30, 110), width=2)
    d.rectangle((cx - 9, cy - 9, cx + 9, cy + 9), outline=(245, 144, 30, 150), width=2)
    d.ellipse(
        (round(width * 0.66), round(height * 0.08), round(width * 0.82), round(height * 0.2)),
        fill=(225, 220, 205, 90),
    )
    return image.convert("RGBA")
