"""The ``tycho`` command: run the talking loop, check the setup, choose a button (ADR 11)."""

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tycho import __version__, credentials, doctor, logs, paths, settings
from tycho.audio.source import AudioError, microphone_device
from tycho.brain_choice import load_brain
from tycho.commands import CommandFileError, CommandMatcher
from tycho.doctor import UNKNOWN_SETTING, diagnose, start_update_check
from tycho.findings import TYCHO
from tycho.games import find_install, load_keys
from tycho.keybinds import Keybinds
from tycho.packs import (
    GameDataError,
    GameKeys,
    GamePack,
    as_run,
    built_in,
)
from tycho.ptt.binding import Binding
from tycho.ptt.conflicts import conflict_warnings
from tycho.ptt.devices import describe, input_sources
from tycho.settings import Settings, SettingsError
from tycho.stt import PARAKEET_V2, ModelError, ModelStore
from tycho.stt.choice import GPU_HELP, MODEL_HELP, device_choice, device_name, gpu_hint
from tycho.voice.choice import load_speaker

if TYPE_CHECKING:  # the runtime pulls in the hands and audio; imported when running
    from tycho.address import Address
    from tycho.audio.source import AudioSource
    from tycho.desktop.window import AppWindow
    from tycho.input import FocusCheck, KeySender
    from tycho.runtime import (
        ActionLog,
        Brain,
        Display,
        Feedback,
        GameState,
        Result,
        Session,
        Speaker,
        Transcriber,
    )
    from tycho.runtime.instance import OpenRequests
    from tycho.spoken_settings import Live
    from tycho.stt.parakeet import ParakeetTranscriber
    from tycho.tile.display import TileDisplay

log = logging.getLogger("tycho")

KEYBIND_REFRESH_SECONDS = 2.0
"""How often to look for keybinds the player changed in game."""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        current = settings.load()
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    if args.command != "check":  # check lists them itself
        for name in settings.unknown_keys():
            print(f"Note: {UNKNOWN_SETTING.format(name=name)}", file=sys.stderr)
    return _dispatch(args, current)


def build_parser() -> argparse.ArgumentParser:
    """The command line. The agent skills in plugins/ are tested against it."""
    parser = argparse.ArgumentParser(
        prog="tycho", description="Tycho, an unofficial voice co-pilot for Star Citizen."
    )
    parser.add_argument("--version", action="version", version=f"Tycho {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    run = commands.add_parser("run", help="start listening (the default)")
    run.add_argument("-v", "--verbose", action="store_true", help="show debug detail")
    run.add_argument(
        "--no-window",
        action="store_true",
        help="the terminal only: no tray icon or window",
    )
    check = commands.add_parser("check", help="check the setup, in plain words")
    check.add_argument("--quick", action="store_true", help="skip the microphone and focus tests")
    check.add_argument(
        "--json", action="store_true", help="print the findings as JSON, for scripts and agents"
    )
    commands.add_parser("bind", help="choose the push-to-talk button")
    commands.add_parser("sounds", help="play the done, not-done and asking sounds")
    commands.add_parser("credits", help="where Tycho's data and speech model come from")
    model = commands.add_parser("model", help="download the speech model now, or see if it's here")
    actions = commands.add_parser(
        "actions", help="find the game's action names, for your own commands.toml"
    )
    actions.add_argument("words", nargs="*", help='words to look for, e.g. "landing gear"')
    model.add_argument("action", choices=["download", "status"])
    gpu = commands.add_parser(
        "gpu", help="download (or remove) what speech recognition needs to use an NVIDIA card"
    )
    gpu.add_argument("action", choices=["install", "status", "remove"])
    key = commands.add_parser("key", help="store, check or remove an API key")
    key.add_argument("action", choices=["set", "check", "delete"])
    key.add_argument("name", choices=credentials.PRODUCT)
    return parser


def _dispatch(args: argparse.Namespace, current: Settings) -> int:
    match args.command:
        case "check":
            if args.json:
                return check_json(current, quick=args.quick)
            return check_command(current, quick=args.quick)
        case "model":
            return model_command(args.action)
        case "actions":
            return actions_command(current, args.words)
        case "bind":
            return 0 if bind_command(current) else 1
        case "sounds":
            return sounds_command(current)
        case "credits":
            return credits_command()
        case "gpu":
            return gpu_command(args.action)
        case "key":
            return credentials.main([args.action, args.name])
        case _:
            return run_command(
                current,
                verbose=getattr(args, "verbose", False),
                window=not getattr(args, "no_window", False),
            )


def notice_file() -> Path:
    """NOTICE: the credits and the unofficial-fan-project notice. The installed app has
    it beside tycho.exe (tools/notices.py puts it there); in development, the repo's."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("NOTICE.txt")
    return Path(__file__).resolve().parents[2] / "NOTICE"


def credits_command() -> int:
    """``tycho credits``: the credits ADR 15 promises, and the not-affiliated notice."""
    try:
        print(notice_file().read_text("utf-8-sig").strip())
    except OSError:
        print(
            "Tycho is an unofficial Star Citizen fan project, not affiliated with the Cloud "
            "Imperium group of companies. Data: the Star Citizen Wiki API, UEX Corp and "
            "starcitizen.tools. Speech: NVIDIA's Parakeet TDT 0.6B v2 (CC-BY-4.0)."
        )
    return 0


# --- tycho run -----------------------------------------------------------------------


WINDOWED = False
"""True in the Start menu's Tycho (tycho-app.exe, #146): no terminal, so nothing can be
asked there, and a problem is shown in a message box (packaging/tycho_main.py)."""


def run_command(current: Settings, *, verbose: bool = False, window: bool = True) -> int:
    from tycho.runtime import AlreadyRunning, SingleInstance
    from tycho.runtime.instance import OpenRequests, ask_to_open

    log_file = logs.setup(verbose=verbose)
    window = window and sys.platform == "win32"
    try:
        with SingleInstance():
            if not window:
                return _run(current, log_file, window=False)
            # Starting Tycho again (the Start menu) shows this one's window (#146).
            with OpenRequests() as requests:
                return _run(current, log_file, window=True, requests=requests)
    except AlreadyRunning as e:
        if window and ask_to_open():
            print("Tycho is already running: its window is open.")
            return 0
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except (FileNotFoundError, GameDataError, AudioError, ModelError, SettingsError) as e:
        log.error("%s", e)
        print(f"Run `{TYCHO} check` for a full diagnosis.", file=sys.stderr)
        return 1


def _run(
    current: Settings,
    log_file: Path,
    *,
    window: bool = True,
    requests: OpenRequests | None = None,
) -> int:
    log.info("Tycho %s. Log file: %s", __version__, log_file)
    start_update_check(current)
    if window and sys.platform == "win32":
        from tycho.desktop.setup import needs_setup

        if needs_setup(current):
            # A first run (no push-to-talk, or no game): the window asks, not the terminal.
            log.info("Opening Tycho's window to set up")
            outcome = first_run(requests)
            if outcome == "closed":
                print("Setup isn't finished, so Tycho isn't listening. Run it again to finish.")
                return 1
            if outcome == "no_window" and WINDOWED:
                print(NO_WINDOW, file=sys.stderr)
                return 1
            if outcome == "no_window":
                # No window on this PC (no WebView2): set up in the terminal, as before.
                print("Tycho's window can't open on this PC, so setup is here in the terminal.")
            current = settings.load()
    # The star while it starts, for a terminal `tycho run` too: a second start then
    # says it's still starting rather than nothing (review of #157).
    reopen = False
    previous: Settings | None = None
    """The settings before a restart, while the new game hasn't started yet (#329)."""
    notices: list[str] = []
    while True:
        starting = _Starting(requests) if window and sys.platform == "win32" else None
        try:
            code = _start(
                current,
                window=window,
                requests=requests,
                starting=starting,
                reopen=reopen,
                notices=notices,
            )
        except GAME_ERRORS as e:
            if previous is None or previous.game == current.game:
                raise
            # The new game didn't start (its folder isn't there): rather than vanish with
            # the window already closed, go back to the game that ran, and say why (#329).
            current, notices = _back_to(previous, current, str(e))
            previous = None
            continue
        finally:
            if starting is not None:
                starting.close()
        if code != RESTART:
            return code
        # A new game was picked in the window (#295): everything loaded at start, again.
        log.info("Restarting with the settings as saved")
        previous, current, notices = current, settings.load(), []
        reopen = True
        if previous.game != current.game and window and sys.platform == "win32":
            from tycho.desktop.setup import needs_setup

            if needs_setup(current):
                # The new game needs setting up (its folder isn't found): the window asks.
                log.info("Opening Tycho's window to set up %s", current.game)
                if first_run(requests) == "done":
                    current = settings.load()
                else:
                    current, notices = _back_to(previous, current, "its setup wasn't finished")
                    previous = None


RESTART = -1
"""``_start``'s answer when the voice loop stopped to start again (#295)."""
GAME_ERRORS = (FileNotFoundError, GameDataError)
"""What a game that can't be started raises: its folder or its files aren't there."""


def _back_to(previous: Settings, current: Settings, why: str) -> tuple[Settings, list[str]]:
    """Save the game that ran before a restart back to the settings, and the notice that
    says why (#329). The window opens again on it, as it was."""
    tried, back = built_in(current.game).name, built_in(previous.game).name
    said = f"Couldn't start {tried} ({why}), so Tycho is back on {back}."
    log.warning("%s", said)
    settings.update({"game": previous.game})
    return settings.load(), [said]


def _start(
    current: Settings,
    *,
    window: bool,
    requests: OpenRequests | None,
    starting: _Starting | None,
    reopen: bool = False,
    notices: Sequence[str] = (),
) -> int:
    """Everything after setup: the speech model, the keybinds, the Tile, the
    microphone, then listening. ``RESTART`` if the window asked to start again;
    ``reopen`` opens the window once listening, as it was before a restart. ``notices``
    are shown on the Tile once it's up (why a new game didn't start, #329)."""
    from tycho.address import Address, Pronouncing
    from tycho.audio.microphone import Microphone
    from tycho.feedback import SoundDevicePlayer, SoundFeedback
    from tycho.input.windows import GAME_EXECUTABLE, WindowsFocusCheck, WindowsKeySender
    from tycho.runtime.actionlog import FileActionLog
    from tycho.tile.cards import notice
    from tycho.tile.display import TileDisplay
    from tycho.voice.elevenlabs import ElevenLabsSpeaker

    binding = current.push_to_talk_binding
    if binding is None and WINDOWED:
        print(NO_BUTTON, file=sys.stderr)  # there's no terminal to choose it in
        return 1
    if binding is None:
        print("First, choose your push-to-talk button.")
        rebound = bind_command(current)
        if rebound is None or (binding := rebound.push_to_talk_binding) is None:
            return 1
        current = rebound

    pack = game_pack(current)
    if starting is not None:
        starting.doing("Reading your keybinds")
    # A pack with no keybinds part (an advisor, #191) has none to read, and no game folder.
    store = load_keybinds(current, binding) if pack.parts.keybinds is not None else None
    transcriber = load_transcriber(current, starting.doing if starting is not None else None)
    action_log = FileActionLog()
    log.info("Action log: %s", action_log.path)
    feedback = SoundFeedback(SoundDevicePlayer(), current.feedback_volume)
    told: list[str] = list(notices)
    """The voice fell back to George (#242), or a game didn't start: shown on the Tile
    once it's up."""

    def voice_fell_back(said: str) -> None:
        if display is None:
            told.append(said)
        else:
            display.show(notice(said))

    display: TileDisplay | None = None
    voice = load_speaker(
        current, voice_fell_back, starting.doing if starting is not None else lambda _now: None
    )
    address = Address.from_settings(current, pack)  # the pack's default name (#349)
    speaker = Pronouncing(voice, address) if voice is not None else None
    # What a setting changed by voice changes in the running session (#111). A name
    # missing here applies at the next start; the session is added once it's built.
    live: Live = {"feedback_volume": lambda v: feedback.set_volume(_float(v))}
    if isinstance(voice, ElevenLabsSpeaker):
        live["voice_volume"] = lambda v: voice.set_volume(_float(v))
    for name in ("call_me", "call_me_says", "call_me_how_often"):
        live[name] = lambda v, name=name: address.set(name, v)
    # The Tile (ADR 16): a window of its own, never anything in the game.
    tile = TileDisplay(current)
    if tile.start():
        display = tile
        for said in told:
            display.show(notice(said))
        for name in Settings.model_fields:
            if name == "tile" or name.startswith("tile_"):
                live[name] = lambda v, name=name: tile.change(name, v)
    with Microphone(device=microphone_device(current.microphone)) as mic:
        log.info("Microphone: %s", mic.name)
        session = build_session(
            current,
            binding,
            store,
            transcriber,
            audio=mic,
            sender=WindowsKeySender(),
            focus=WindowsFocusCheck(pack.window or GAME_EXECUTABLE),
            feedback=feedback,
            action_log=action_log,
            make_brain=lambda matcher: load_brain(
                matcher, current, live, address, display, pack=pack
            ),
            speaker=speaker,
            address=address,
            display=display,
            pack=pack,
        )
        live["confirm_seconds"] = lambda v: session.set_confirm_seconds(_float(v))
        log.info("Listening. Hold %s and speak; press Ctrl+C to stop.", describe(binding))
        if starting is not None:
            starting.close()  # the tray's own star takes its place
        restart = False
        try:
            if window and sys.platform == "win32":
                outcome = serve_with_tray(session, store, live, display, requests, reopen=reopen)
                if outcome == "failed":
                    return 1
                restart = outcome == "restart"
            else:
                asyncio.run(serve(session, store))
        finally:
            action_log.close()
            if display is not None:
                display.close()
    if mic.overflows:
        log.warning("The microphone dropped audio %d time(s)", mic.overflows)
    return RESTART if restart else 0


NO_WINDOW = (
    "Tycho's window can't open on this PC: it needs Microsoft Edge WebView2, which "
    "Windows 10 and 11 usually have. Install it from Microsoft "
    "(go.microsoft.com/fwlink/p/?LinkId=2124703), or run `tycho run` in a terminal "
    "to set up there."
)
NO_BUTTON = (
    "Tycho has no push-to-talk button yet. Open Tycho and choose one in setup, or run "
    "`tycho bind` in a terminal."
)
NO_TRAY = (
    "Tycho couldn't put its icon by the clock ({why}), so it stopped: without it, there "
    "would be no way to close it."
)


def serve_with_tray(
    session: Session,
    store: GameKeys | None,
    live: Live,
    display: TileDisplay | None,
    requests: OpenRequests | None = None,
    *,
    reopen: bool = False,
) -> Literal["stopped", "restart", "failed"]:
    """Run the session with Tycho's tray icon (ADR 17): pause, the Tile, the logs, quit.
    Without a tray (it couldn't start), the terminal only, as before; with no terminal
    either (the Start menu's Tycho), it stops, says why and returns "failed". "restart":
    the window picked a new game (#295), so the caller starts everything again;
    ``reopen`` opens the window at once, as it was before that restart."""
    import os

    from tycho.desktop.host import Host
    from tycho.desktop.menu import TrayMenu
    from tycho.desktop.tray import TrayIcon

    host = Host(lambda stop: serve(session, store, stop))

    def apply(name: str, value: object) -> bool:
        """A change made in the app, applied as voice applies one: on the voice loop.
        False if nothing running can apply it; it applies at the next start."""
        callback = live.get(name)
        return callback is not None and host.run_now(lambda: callback(value))

    window = app_window(
        apply=apply,
        ship_accent=display.ship_accent if display is not None else lambda: None,
        restart=host.restart,
    )
    host.on_quit = window.close
    host.window_open = lambda: window.is_open
    host.focus_window = window.focus
    menu = TrayMenu(
        session,
        host,
        display=display,
        save=settings.update,
        apply_tile=live.get("tile", lambda _on: None),
        open_logs=lambda: os.startfile(paths.logs_dir()),  # noqa: S606 - Explorer, Tycho's folder
        window=True,
    )
    tray = TrayIcon(menu.tip, menu.items, menu.chosen)
    menu.refresh = tray.refresh
    if not tray.start():
        if WINDOWED:
            log.error("No tray icon (%s), and no terminal: stopping", tray.failed)
            print(NO_TRAY.format(why=tray.failed), file=sys.stderr)
            return "failed"
        log.warning("No tray icon (%s); press Ctrl+C to stop", tray.failed)
        asyncio.run(serve(session, store))
        return "stopped"
    if requests is not None:
        requests.target = lambda: host.ask("open")
    if reopen:
        host.ask("open")  # the window asked for the restart: it's back, on the new loop
    try:
        host.run(window.open)
    finally:
        if requests is not None:
            requests.target = lambda: None
        tray.close()
    return "restart" if host.restarting else "stopped"


def app_window(
    *,
    apply: Callable[[str, object], bool] = lambda _name, _value: False,
    ship_accent: Callable[[], str | None] = lambda: None,
    first_run: bool = False,
    finish: Callable[[], None] = lambda: None,
    restart: Callable[[], bool] = lambda: False,
) -> AppWindow:
    """Tycho's window and its bridge, for the first run and for the tray alike."""
    import dataclasses

    from tycho.desktop.bridge import Bridge
    from tycho.desktop.keys import ProviderKeyCheck
    from tycho.desktop.setup import WindowsPC
    from tycho.desktop.window import AppWindow

    def findings() -> list[Mapping[str, str]]:
        current = settings.load()
        return [dataclasses.asdict(f) for f in doctor.checks(current, quick=True, ask=False)]

    bridge = Bridge(
        apply=apply,
        findings=findings,
        pc=WindowsPC(rescan=first_run),
        key_check=ProviderKeyCheck(),
        ship_accent=ship_accent,
        choose_folder=lambda: window.choose_folder(),
        finish=finish,
        first_run=first_run,
        restart=restart,
    )
    window = AppWindow(bridge)
    return window


class _Starting:
    """The Start menu's Tycho, between setup and listening (review of #157). The speech
    model can take minutes to download, and nothing else shows: a star by the clock
    says what's happening, and a second start says Tycho is still starting."""

    def __init__(self, requests: OpenRequests | None) -> None:
        from tycho.desktop.tray import SEPARATOR, Item, TrayIcon

        self._now = "Starting"
        self._tray: TrayIcon | None = TrayIcon(
            lambda: f"Tycho: {self._now}…",
            lambda: [
                Item("starting", f"{self._now}…", enabled=False),
                SEPARATOR,
                Item("quit", "Quit Tycho"),
            ],
            self._chosen,
        )
        if not self._tray.start():
            self._tray = None
        self._requests = requests
        if requests is not None:
            requests.target = self.say

    def _chosen(self, key: str) -> None:
        """Quit while starting: the 2.5 GB first download can take minutes, and there's
        nothing else to stop it with. A half-downloaded file is left as .part and
        checked before use, so the next start carries on safely."""
        if key == "quit":
            log.info("Quit while starting")
            if sys.stdout is not None and sys.stdout.isatty():
                print("\nStopped.", flush=True)  # after the download's progress line
            os._exit(0)  # the main thread is inside the download, which can't be told

    def doing(self, now: str) -> None:
        self._now = now
        if self._tray is not None:
            self._tray.refresh()

    def say(self) -> None:
        """A second start, while this one starts: on the event's thread, so waiting on
        the box blocks nothing else."""
        from tycho import win32

        text = (
            f"Tycho is still starting: {self._now.lower()}. Its window opens from the "
            "star by the clock once it's listening."
        )
        # MB_ICONINFORMATION, MB_SETFOREGROUND and MB_TOPMOST: in front of the game.
        win32.message_box(text, "Tycho", 0x40 | 0x10000 | 0x40000)

    def close(self) -> None:
        if self._requests is not None and self._requests.target == self.say:
            self._requests.target = lambda: None
        if self._tray is not None:
            self._tray.close()
            self._tray = None


type FirstRun = Literal["done", "closed", "no_window"]


def first_run(requests: OpenRequests | None = None) -> FirstRun:
    """The window, on setup, before Tycho starts listening (ADR 17): "done" once the
    player finishes it, "closed" if they close it first, "no_window" if it can't open.
    Starting Tycho again meanwhile brings this window forward."""
    import threading

    finished = threading.Event()
    opened: list[AppWindow] = []

    def finish() -> None:
        finished.set()
        # A moment for the page to say "Starting Tycho…" before it goes.
        threading.Timer(0.8, opened[0].close).start()

    window = app_window(first_run=True, finish=finish)
    opened.append(window)
    if requests is not None:
        requests.target = window.focus
    try:
        with _ctrl_c(window.close):  # the main thread is inside the window: Ctrl+C closes it
            if not window.open():
                return "no_window"
    finally:
        if requests is not None:
            requests.target = lambda: None
    return "done" if finished.is_set() else "closed"


def _ctrl_c(on_ctrl_c: Callable[[], None]) -> contextlib.AbstractContextManager[object]:
    if sys.platform != "win32":
        return contextlib.nullcontext()
    from tycho.runtime.console import CtrlCGuard

    return CtrlCGuard(on_ctrl_c)


def _float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"expected a number, not {value!r}")
    return float(value)


def game_pack(current: Settings) -> GamePack:
    """The game the settings pick (ADR 18), as the player's ``key_actions`` runs it (#191),
    named in the log with its version, its mode and the hash of its briefing, so a bug
    report or a score says which pack it came from."""
    from tycho.brain.briefing import instructions_hash

    pack = as_run(built_in(current.game), key_actions=current.key_actions)
    log.info(
        "Game: %s, pack %s, key actions %s (briefing %s)",
        pack.name,
        pack.version,
        "off" if pack.safety.keys_off else "on",
        instructions_hash(pack),
    )
    return pack


def load_keybinds(current: Settings, binding: Binding) -> GameKeys:
    install = find_install(current)
    store = load_keys(install)
    log.info("Keybinds: %d actions from %s", len(store.keybinds), install.folder)
    for warning in conflict_warnings(binding, store):
        log.warning("%s", warning)
    return store


def load_transcriber(
    current: Settings, doing: Callable[[str], None] | None = None
) -> ParakeetTranscriber:
    """``doing`` hears what's happening, for the star of a Tycho with no terminal."""
    from tycho.stt.parakeet import ParakeetTranscriber

    folder = ModelStore(paths.models_dir()).ensure(PARAKEET_V2, _download_progress(doing=doing))
    if doing is not None:
        doing("Loading the speech model")
    choice = device_choice(current)
    log.info("Speech recognition on the %s: %s", device_name(choice.device), choice.reason)
    started = time.monotonic()
    transcriber = ParakeetTranscriber.load(folder, choice.device)
    log.info("Speech model ready in %.1f s", time.monotonic() - started)
    wanted = current.transcription_device != "cpu"
    if transcriber.device == "cpu" and wanted and (hint := gpu_hint()):
        log.info("%s", hint)
    return transcriber


MAX_ACTIONS_SHOWN = 40


def actions_command(current: Settings, words: list[str]) -> int:
    """``tycho actions``: the game's actions whose name or label has every word given, with
    their keys, so a player (or their agent) can write commands.toml with the right names."""
    game = built_in(current.game)
    if game.parts.keybinds is None:  # an advisor pack: no keys to read, or press (#338)
        print(f"{game.name} has no actions: Tycho presses no keys there, it only answers.")
        return 1
    try:
        keybinds = load_keys(find_install(current)).keybinds
    except (FileNotFoundError, GameDataError) as e:
        print(e, file=sys.stderr)
        return 2
    wanted = [w.lower() for w in words]
    found = [
        a
        for a in keybinds
        if all(w in f"{a.name} {a.label}".lower().replace("_", " ") for w in wanted)
    ]
    for action in found[:MAX_ACTIONS_SHOWN]:
        keys = ", ".join(str(k) for k in action.inputs) or "no key"
        print(f"{action.name}  |  {action.label or '(no label)'}  |  {keys}  |  {action.actionmap}")
    if len(found) > MAX_ACTIONS_SHOWN:
        print(f"... and {len(found) - MAX_ACTIONS_SHOWN} more: add a word to narrow it down.")
    # The game's names often aren't the player's ("Landing System", not "landing gear"),
    # so Tycho's own commands that are said with those words are shown too.
    try:
        pack = built_in(current.game)
        matcher = CommandMatcher.default(paths.commands_file(pack.id), pack=pack)
    except CommandFileError:  # tycho check explains it; the built-in ones still help here
        matcher = CommandMatcher.default(pack=built_in(current.game))
    said = [
        c
        for c in matcher.commands
        if wanted and any(all(w in phrase for w in wanted) for phrase in c.say)
    ]
    if said:
        print("Tycho's commands said that way (their actions, best first):")
        for command in said[:MAX_ACTIONS_SHOWN]:
            parts = command.actions or tuple(f"[{s.command.id}]" for s in command.steps)
            print(f"  {command.id}: {', '.join(parts)}")
    if not found and not said:
        print("No action has all of those words. Try fewer, or other words for it.")
    return 0 if found or said else 1


def model_command(action: str) -> int:
    """``tycho model``: get the speech model before the first ``tycho run`` (a setup step an
    agent can do for the player), or say whether it's here."""
    model = ModelStore(paths.models_dir())
    folder = model.folder(PARAKEET_V2)
    if action == "status":
        if model.ready(PARAKEET_V2) or not model.missing(PARAKEET_V2):
            print(f"Downloaded and checked, in {folder}.")
            return 0
        print(f"Not downloaded yet ({PARAKEET_V2.size / 1e9:.1f} GB). Run: {MODEL_HELP}")
        return 1
    print(f"Getting the speech model ({PARAKEET_V2.size / 1e9:.1f} GB) into {folder}.")
    try:
        model.ensure(PARAKEET_V2, _download_progress())
    except ModelError as e:
        print(e, file=sys.stderr)
        return 2
    print("Done. The speech model is downloaded and checked.")
    return 0


def gpu_command(action: str) -> int:
    """``tycho gpu``: NVIDIA's CUDA libraries, so speech recognition can use the card."""
    from tycho.stt import CudaLibraries, ModelError, nvidia_driver
    from tycho.stt.cuda import DOWNLOAD_BYTES, MIN_DRIVER

    libraries = CudaLibraries(paths.cuda_dir())
    if action == "status":
        if libraries.ready():
            print(f"Installed in {libraries.root}. Speech recognition can use the graphics card.")
            return 0
        print(f"Not installed ({DOWNLOAD_BYTES / 1e9:.1f} GB to download). Run: {GPU_HELP}")
        return 1
    if action == "remove":
        from tycho.runtime.instance import AlreadyRunning, SingleInstance

        # A running Tycho has the DLLs loaded, and deleting half of them leaves neither
        # the graphics card nor a quick reinstall (audit, 2026-11-03).
        try:
            with SingleInstance():
                print("Removed." if libraries.remove() else "Nothing to remove.")
        except AlreadyRunning:
            print("Tycho is running and using these libraries. Close it first.", file=sys.stderr)
            return 2
        except ModelError as e:
            print(e, file=sys.stderr)
            return 2
        return 0
    driver = nvidia_driver()
    if driver is None:
        print("No NVIDIA graphics card found, so speech recognition runs on the processor.")
        return 1
    if driver < MIN_DRIVER:
        print(f"Update the NVIDIA driver to {MIN_DRIVER} or newer first (you have {driver}).")
        return 1
    print(f"Downloading NVIDIA's CUDA libraries ({DOWNLOAD_BYTES / 1e9:.1f} GB), once.")
    try:
        libraries.ensure(_download_progress("the graphics card libraries"))
    except ModelError as e:
        print(e, file=sys.stderr)
        return 2
    print("Done. Speech recognition will use the graphics card the next time Tycho starts.")
    return 0


def load_commands(pack: GamePack) -> CommandMatcher:
    """The game's built-in commands plus the player's own file for that game. A broken
    file is reported and skipped, so a typo never stops Tycho starting."""
    try:
        matcher = CommandMatcher.default(paths.commands_file(pack.id), pack=pack)
    except CommandFileError as e:
        log.warning("Your commands file can't be used, so only the built-in ones are: %s", e)
        return CommandMatcher.default(pack=pack)
    for warning in matcher.warnings:
        log.warning("Your commands file: %s", warning)
    return matcher


def build_session(
    current: Settings,
    binding: Binding,
    store: GameKeys | None,
    transcriber: Transcriber,
    *,
    audio: AudioSource,
    sender: KeySender,
    focus: FocusCheck,
    feedback: Feedback,
    game_state: GameState | None = None,
    action_log: ActionLog | None = None,
    make_brain: Callable[[CommandMatcher], Brain | None] | None = None,
    speaker: Speaker | None = None,
    on_result: Callable[[Result], None] | None = None,
    address: Address | None = None,
    display: Display | None = None,
    pack: GamePack,
) -> Session:
    """The talking loop, wired the way ``tycho run`` wires it, for ``pack``'s game: its
    commands, words and safety lists, which say whether key actions are off (#191).
    ``store`` is None for a pack with no keybinds part."""
    from tycho.games import BUILT_IN
    from tycho.input import Presser
    from tycho.lexicon import Lexicon
    from tycho.ptt.monitor import PushToTalk
    from tycho.runtime import Session

    if game_state is None and store is not None and pack.parts.game_state is not None:
        game_state = BUILT_IN.game_state[pack.parts.game_state](store.install.folder)
    matcher = load_commands(pack)
    return Session(
        audio=audio,
        push_to_talk=PushToTalk(binding, input_sources(binding)),
        transcriber=transcriber,
        lexicon=Lexicon.default(pack),
        matcher=matcher,
        keybinds=(lambda: store.keybinds) if store is not None else lambda: NO_KEYBINDS,
        presser=Presser(sender, focus, game=pack.name),
        feedback=feedback,
        game_state=game_state,
        action_log=action_log,
        brain=make_brain(matcher) if make_brain is not None else None,
        speaker=speaker,
        brain_seconds=current.brain_seconds,
        search_seconds=_search_seconds(current.web_search_open)[0],
        search_limit=_search_seconds(current.web_search_open)[1],
        confirm_seconds=current.confirm_seconds,
        log_transcripts=current.keep_transcripts,
        on_result=on_result,
        address=(lambda: address.name) if address is not None else (lambda: ""),
        display=display,
        safety_lists=pack.safety,
    )


def _search_seconds(open_web: bool) -> tuple[float, float]:
    """How long the brain has once a search starts, and the request's limit. The
    open-web fallback (#354) searches twice and reads pages: it gets longer."""
    from tycho.runtime import session

    if open_web:
        return session.OPEN_SEARCH_SECONDS, session.OPEN_SEARCH_LIMIT
    return session.SEARCH_SECONDS, session.SEARCH_LIMIT


NO_KEYBINDS = Keybinds([])
"""A pack with no keybinds part's keys (an advisor, #191): none."""


async def serve(
    session: Session, store: GameKeys | None, stop: asyncio.Event | None = None
) -> None:
    """Run the session until ``stop``, Ctrl+C, or the console window closing. ``store``
    is reread now and then, if the pack has keybinds."""
    from tycho.runtime.console import CloseGuard

    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    refresher = asyncio.create_task(
        _refresh_keybinds(store, session.warm_brain) if store is not None else stop.wait()
    )

    def request_stop() -> None:
        loop.call_soon_threadsafe(stop.set)

    with CloseGuard(request_stop):
        try:
            await session.run(stop)
        finally:
            refresher.cancel()
            await asyncio.gather(refresher, return_exceptions=True)


async def _refresh_keybinds(store: GameKeys, changed: Callable[[], None]) -> None:
    """Reread the keybinds now and then; ``changed`` is called when they change, to warm
    the brain's cache for the new briefing (#60), once they've settled: the game writes
    the file once per rebind, so a player rebinding several keys would warm it each time
    (#352). The new keys are used at once either way."""
    last_error: str | None = None
    settling = False
    while True:
        await asyncio.sleep(KEYBIND_REFRESH_SECONDS)
        if await asyncio.to_thread(store.refresh):
            settling = True
        elif settling:
            settling = False
            log.info("Keybinds changed in game; using the new ones")
            changed()
        if store.last_error and store.last_error != last_error:
            log.warning(
                "Couldn't reread keybinds (%s); keeping the last good ones", store.last_error
            )
        last_error = store.last_error


def _download_progress(
    what: str = "the speech model", doing: Callable[[str], None] | None = None
) -> Callable[[str, int, int], None]:
    """One line per file, updated in place, ending when the file is complete; and, with
    ``doing``, the same for the star's tooltip."""
    shown: dict[str, int] = {}

    def show(name: str, done: int, total: int) -> None:
        percent = 100 * done // total if total else 0
        if shown.get(name) == percent:
            return
        shown[name] = percent
        size = f"{done / 1e9:.2f} of {total / 1e9:.2f} GB" if total > 1e8 else ""
        end = "\n" if done >= total else ""
        line = f"  Downloading {what}: {name} {percent:3d}% {size}"
        print("\r" + line, end=end, flush=True)  # \r: overwrite the line in place
        if doing is not None and total > 1e8:  # the big file is the wait
            doing(f"Getting {what} (2.5 GB, once): {percent}%")

    return show


# --- tycho check ---------------------------------------------------------------------


def check_command(current: Settings, *, quick: bool = False) -> int:
    findings = list(diagnose(current, quick=quick))
    failed = sum(f.level == "fail" for f in findings)
    warned = sum(f.level == "warn" for f in findings)
    print()
    if failed:
        print(f"{failed} problem(s) to fix before Tycho can run.")
    elif warned:
        print(f"Ready, with {warned} thing(s) worth a look.")
    else:
        print(f"All good. Start Tycho with: {TYCHO} run")
    return 1 if failed else 0


def check_json(current: Settings, *, quick: bool = False) -> int:
    """``tycho check --json``: the same checks, as one JSON document on stdout, so an agent
    helping the player set up doesn't have to read the printed text."""
    import json

    findings = list(doctor.checks(current, quick))
    failed = any(f.level == "fail" for f in findings)
    document = {
        "tycho": __version__,
        "ready": not failed,
        "findings": [
            {"level": f.level, "what": f.what, "detail": f.detail, "fix": f.fix or None}
            for f in findings
        ],
    }
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 1 if failed else 0


# --- tycho bind and tycho sounds -----------------------------------------------------


def bind_command(current: Settings) -> Settings | None:
    from tycho.ptt import windows as win
    from tycho.ptt.monitor import capture_binding

    sources = input_sources(None)
    print("Press the button you want to hold while talking: a keyboard key, a mouse")
    print("button (not left click) or a joystick button. A mouse side button works well.")
    found = capture_binding(sources, ignore=frozenset({win.LEFT_MOUSE}), timeout=30)
    if found is None:
        print("No button pressed within 30 seconds; nothing changed.")
        return None
    updated = current.model_copy(update={"push_to_talk": str(found)})
    path = settings.save(updated)
    print(f"Push-to-talk is now {describe(found)}. Saved to {path}")
    return updated


def sounds_command(current: Settings) -> int:
    from tycho.feedback import SoundDevicePlayer, SoundFeedback

    feedback = SoundFeedback(SoundDevicePlayer(), current.feedback_volume)
    print(f"Volume {current.feedback_volume:.0%}. Change it with feedback_volume in")
    print(f"{paths.settings_file()}\n")
    sounds = (
        ("Done", feedback.done),
        ("Not done", feedback.not_done),
        ('Asking (say "confirm" to go ahead)', feedback.ask),
    )
    for name, play in sounds:
        print(f"  {name}...")
        play()
        time.sleep(1.2)
    return 0


# --- Shared pieces -------------------------------------------------------------------


if __name__ == "__main__":
    sys.exit(main())
