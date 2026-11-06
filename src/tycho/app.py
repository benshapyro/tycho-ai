"""The ``tycho`` command: run the talking loop, check the setup, choose a button (ADR 11)."""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from tycho import __version__, credentials, logs, paths, settings, updates
from tycho.audio.source import AudioError
from tycho.commands import CommandFileError, CommandMatcher, resolve
from tycho.game.install import GameInstall, root_from, search_roots
from tycho.game.store import GameDataError, KeybindStore
from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.monitor import InputSource
from tycho.settings import Settings, SettingsError
from tycho.stt import PARAKEET_V2, DeviceChoice, ModelError, ModelStore

if TYPE_CHECKING:  # the runtime pulls in the hands and audio; imported when running
    from tycho.address import Address
    from tycho.audio.source import AudioSource
    from tycho.desktop.window import AppWindow
    from tycho.input import FocusCheck, KeySender
    from tycho.knowledge.ships import ShipLibrary
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
    from tycho.spoken_settings import Live
    from tycho.stt.parakeet import ParakeetTranscriber
    from tycho.tile.display import TileDisplay

log = logging.getLogger("tycho")

TYCHO = "tycho" if getattr(sys, "frozen", False) else "uv run tycho"
"""How to run Tycho, for messages: installed, it's `tycho` (on PATH and the Start menu)."""

UNKNOWN_SETTING = (
    "{name} in settings.toml isn't a setting this Tycho knows, so it's ignored. It may be a "
    "typo, or from a newer Tycho."
)

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
    key.add_argument("name", choices=credentials.KNOWN)
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


def run_command(current: Settings, *, verbose: bool = False, window: bool = True) -> int:
    from tycho.runtime import AlreadyRunning, SingleInstance

    log_file = logs.setup(verbose=verbose)
    try:
        with SingleInstance():
            return _run(current, log_file, window=window)
    except AlreadyRunning as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except (FileNotFoundError, GameDataError, AudioError, ModelError, SettingsError) as e:
        log.error("%s", e)
        print(f"Run `{TYCHO} check` for a full diagnosis.", file=sys.stderr)
        return 1


def _run(current: Settings, log_file: Path, *, window: bool = True) -> int:
    from tycho.address import Address, Pronouncing
    from tycho.audio.microphone import Microphone
    from tycho.feedback import SoundDevicePlayer, SoundFeedback
    from tycho.input.windows import WindowsFocusCheck, WindowsKeySender
    from tycho.runtime.actionlog import FileActionLog
    from tycho.tile.display import TileDisplay
    from tycho.voice.elevenlabs import ElevenLabsSpeaker

    log.info("Tycho %s. Log file: %s", __version__, log_file)
    start_update_check(current)
    if window and sys.platform == "win32":
        from tycho.desktop.setup import needs_setup

        if needs_setup(current):
            # A first run (no push-to-talk, or no game): the window asks, not the terminal.
            log.info("Opening Tycho's window to set up")
            outcome = first_run()
            if outcome == "closed":
                print("Setup isn't finished, so Tycho isn't listening. Run it again to finish.")
                return 1
            if outcome == "no_window":
                # No window on this PC (no WebView2): set up in the terminal, as before.
                print("Tycho's window can't open on this PC, so setup is here in the terminal.")
            current = settings.load()
    binding = current.push_to_talk_binding
    if binding is None:
        print("First, choose your push-to-talk button.")
        rebound = bind_command(current)
        if rebound is None or (binding := rebound.push_to_talk_binding) is None:
            return 1
        current = rebound

    store = load_keybinds(current, binding)
    transcriber = load_transcriber(current)
    action_log = FileActionLog()
    log.info("Action log: %s", action_log.path)
    feedback = SoundFeedback(SoundDevicePlayer(), current.feedback_volume)
    voice = load_speaker(current)
    address = Address.from_settings(current)
    speaker = Pronouncing(voice, address) if voice is not None else None
    # What a setting changed by voice changes in the running session (#111). A name
    # missing here applies at the next start; the session is added once it's built.
    live: Live = {"feedback_volume": lambda v: feedback.set_volume(_float(v))}
    if isinstance(voice, ElevenLabsSpeaker):
        live["voice_volume"] = lambda v: voice.set_volume(_float(v))
    for name in ("call_me", "call_me_says", "call_me_how_often"):
        live[name] = lambda v, name=name: address.set(name, v)
    # The Tile (ADR 16): a window of its own, never anything in the game.
    display: TileDisplay | None = TileDisplay(current)
    if not display.start():
        display = None
    else:
        for name in Settings.model_fields:
            if name == "tile" or name.startswith("tile_"):
                live[name] = lambda v, name=name: display.change(name, v)
    with Microphone(device=microphone_device(current.microphone)) as mic:
        log.info("Microphone: %s", mic.name)
        session = build_session(
            current,
            binding,
            store,
            transcriber,
            audio=mic,
            sender=WindowsKeySender(),
            focus=WindowsFocusCheck(),
            feedback=feedback,
            action_log=action_log,
            make_brain=lambda matcher: load_brain(matcher, current, live, address, display),
            speaker=speaker,
            address=address,
            display=display,
        )
        live["confirm_seconds"] = lambda v: session.set_confirm_seconds(_float(v))
        log.info("Listening. Hold %s and speak; press Ctrl+C to stop.", describe(binding))
        try:
            if window and sys.platform == "win32":
                serve_with_tray(session, store, live, display)
            else:
                asyncio.run(serve(session, store))
        finally:
            action_log.close()
            if display is not None:
                display.close()
    if mic.overflows:
        log.warning("The microphone dropped audio %d time(s)", mic.overflows)
    return 0


def serve_with_tray(
    session: Session, store: KeybindStore, live: Live, display: TileDisplay | None
) -> None:
    """Run the session with Tycho's tray icon (ADR 17): pause, the Tile, the logs, quit.
    Without a tray (it couldn't start), the terminal only, as before."""
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
        apply=apply, ship_accent=display.ship_accent if display is not None else lambda: None
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
        log.warning("No tray icon (%s); press Ctrl+C to stop", tray.failed)
        asyncio.run(serve(session, store))
        return
    try:
        host.run(window.open)
    finally:
        tray.close()


def app_window(
    *,
    apply: Callable[[str, object], bool] = lambda _name, _value: False,
    ship_accent: Callable[[], str | None] = lambda: None,
    first_run: bool = False,
    finish: Callable[[], None] = lambda: None,
) -> AppWindow:
    """Tycho's window and its bridge, for the first run and for the tray alike."""
    import dataclasses

    from tycho.desktop.bridge import Bridge
    from tycho.desktop.keys import ProviderKeyCheck
    from tycho.desktop.window import AppWindow

    def findings() -> list[Mapping[str, str]]:
        current = settings.load()
        return [dataclasses.asdict(f) for f in _checks(current, quick=True, ask=False)]

    bridge = Bridge(
        apply=apply,
        findings=findings,
        key_check=ProviderKeyCheck(),
        ship_accent=ship_accent,
        choose_folder=lambda: window.choose_folder(),
        finish=finish,
        first_run=first_run,
    )
    window = AppWindow(bridge)
    return window


type FirstRun = Literal["done", "closed", "no_window"]


def first_run() -> FirstRun:
    """The window, on setup, before Tycho starts listening (ADR 17): "done" once the
    player finishes it, "closed" if they close it first, "no_window" if it can't open."""
    import threading

    finished = threading.Event()
    opened: list[AppWindow] = []

    def finish() -> None:
        finished.set()
        # A moment for the page to say "Starting Tycho…" before it goes.
        threading.Timer(0.8, opened[0].close).start()

    window = app_window(first_run=True, finish=finish)
    opened.append(window)
    if not window.open():
        return "no_window"
    return "done" if finished.is_set() else "closed"


def _float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"expected a number, not {value!r}")
    return float(value)


def load_keybinds(current: Settings, binding: Binding) -> KeybindStore:
    install = find_install(current)
    store = KeybindStore.load(install)
    log.info("Keybinds: %d actions from %s", len(store.keybinds), install.folder)
    for warning in conflict_warnings(binding, store):
        log.warning("%s", warning)
    return store


def load_transcriber(current: Settings) -> ParakeetTranscriber:
    from tycho.stt.parakeet import ParakeetTranscriber

    folder = ModelStore(paths.models_dir()).ensure(PARAKEET_V2, _download_progress())
    choice = device_choice(current)
    log.info("Speech recognition on the %s: %s", _device_name(choice.device), choice.reason)
    started = time.monotonic()
    transcriber = ParakeetTranscriber.load(folder, choice.device)
    log.info("Speech model ready in %.1f s", time.monotonic() - started)
    wanted = current.transcription_device != "cpu"
    if transcriber.device == "cpu" and wanted and (hint := gpu_hint()):
        log.info("%s", hint)
    return transcriber


def speech_finding(current: Settings) -> Finding:
    """Where speech recognition runs, and how to move it to the graphics card if it
    could be there (audit, 2026-11-03: check warned without saying what to do)."""
    from tycho.stt import nvidia_driver

    choice = device_choice(current)
    detail = f"on the {_device_name(choice.device)}: {choice.reason}"
    if choice.device == "gpu" or current.transcription_device == "cpu":
        return Finding("ok", "Speech recognition", detail)
    if hint := gpu_hint():
        fix = GPU_HELP if hint.endswith(GPU_HELP) else ""
        return Finding("warn", "Speech recognition", f"{detail}. {hint}", fix=fix)
    if nvidia_driver() is None:  # nothing to do about it: fine, just slower
        return Finding("ok", "Speech recognition", "on the processor (no NVIDIA card)")
    return Finding("warn", "Speech recognition", detail)


def gpu_hint() -> str | None:
    """What to do when an NVIDIA card could run speech recognition but its libraries are
    missing; None if there's no NVIDIA card or it already has them (downloaded, or the
    pip wheels in development), so a card that's merely short of memory gets no hint."""
    import importlib.util

    from tycho.stt import CudaLibraries, nvidia_driver
    from tycho.stt.cuda import DOWNLOAD_BYTES, MIN_DRIVER

    driver = nvidia_driver()
    if driver is None or CudaLibraries(paths.cuda_dir()).ready():
        return None
    if importlib.util.find_spec("nvidia") is not None:
        return None
    if driver < MIN_DRIVER:
        return (
            f"Your NVIDIA card could recognise speech about 10x faster, but needs driver "
            f"{MIN_DRIVER} or newer (you have {driver}). Update it, then run: {GPU_HELP}"
        )
    return (
        "Speech recognition is on the processor. Your NVIDIA card can do it about 10x "
        f"faster with a one-time {DOWNLOAD_BYTES / 1e9:.1f} GB download: {GPU_HELP}"
    )


GPU_HELP = f"{TYCHO} gpu install"
MODEL_HELP = f"{TYCHO} model download"


MAX_ACTIONS_SHOWN = 40


def actions_command(current: Settings, words: list[str]) -> int:
    """``tycho actions``: the game's actions whose name or label has every word given, with
    their keys, so a player (or their agent) can write commands.toml with the right names."""
    try:
        keybinds = KeybindStore.load(find_install(current)).keybinds
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
        matcher = CommandMatcher.default(paths.commands_file())
    except CommandFileError:  # tycho check explains it; the built-in ones still help here
        matcher = CommandMatcher.default()
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


BRAIN_KEY_HELP = f"{TYCHO} key set anthropic"


def anthropic_key() -> str | None:
    from tycho import credentials

    try:
        return credentials.get("anthropic")
    except credentials.CredentialStoreError as e:
        log.warning("Couldn't read your Anthropic key: %s", e)
        return None


def load_brain(
    matcher: CommandMatcher,
    current: Settings,
    live: Live | None = None,
    address: Address | None = None,
    display: TileDisplay | None = None,
) -> Brain | None:
    """Claude, if the player has stored an Anthropic key. Without one, the fast path only.
    ``live`` applies settings changed by voice to the running session; ``address`` is
    what to call the player (#15), read fresh for each request. With a ``display``,
    Claude may show cards while the Tile is on, in the colour of the ship being flown."""
    key = anthropic_key()
    if key is None:
        log.info(
            "No Anthropic key, so only the built-in phrasings work. Add one: %s", BRAIN_KEY_HELP
        )
        return None
    import anthropic

    from tycho import paths
    from tycho.brain.claude import MODEL, ClaudeBrain
    from tycho.brain.lookups import Lookup
    from tycho.knowledge import ShipLookup, UexApi, WikiApi
    from tycho.knowledge.fleet import Fleet, FleetUpdate
    from tycho.knowledge.item_lookup import ItemLookup
    from tycho.knowledge.trade_lookup import trade_lookups
    from tycho.knowledge.web import WebSearch
    from tycho.spoken_settings import SettingsTool

    log.info("Brain: %s, for anything the built-in phrasings don't cover", MODEL)
    if not current.web_search:
        log.info("Web search: off (web_search is false)")
    ships = ShipLookup(WikiApi(), paths.knowledge_dir(), concepts=UexApi())
    fleet = Fleet.load(paths.fleet_file())
    lookups: list[Lookup] = [
        ships,
        FleetUpdate(fleet, lambda: ships.library),
        ItemLookup(WikiApi()),
        *trade_lookups(UexApi()),
        SettingsTool(live=live),
    ]
    if current.web_search:
        lookups.append(
            WebSearch(
                anthropic.AsyncAnthropic(api_key=key, max_retries=0),
                log_queries=current.keep_transcripts,
            )
        )

    def about() -> str:
        parts = [fleet.describe(ships.library), address.header() if address else ""]
        return "\n".join(p for p in parts if p)

    if display is not None:
        display.ship_accent = lambda: ship_accent(fleet.flying, ships.library)
    return ClaudeBrain(
        matcher,
        api_key=key,
        lookups=lookups,
        about=about,
        cards=lambda: display is not None and display.on,
    )


def ship_accent(flying: str, library: ShipLibrary | None) -> str | None:
    """The Tile's colour for the ship the player said they're flying, if Tycho knows it."""
    from tycho.knowledge.ships import Ship
    from tycho.tile.design import MAKER_ACCENTS, accent_for, accent_for_maker

    if not flying or library is None:
        return None
    found = library.find(flying)
    if not isinstance(found, Ship):
        return None
    if found.key.split("_", 1)[0].upper() in MAKER_ACCENTS:
        return accent_for(found.key)
    return accent_for_maker(found.maker)


VOICE_KEY_HELP = f"{TYCHO} key set elevenlabs"


def elevenlabs_key() -> str | None:
    from tycho import credentials

    try:
        return credentials.get("elevenlabs")
    except credentials.CredentialStoreError as e:
        log.warning("Couldn't read your ElevenLabs key: %s", e)
        return None


def load_speaker(current: Settings) -> Speaker | None:
    """ElevenLabs, if the player has stored a key and the voice isn't turned off.
    Without it, Tycho's replies are shown in its window and it plays its sounds."""
    if current.voice_volume == 0:
        log.info("Voice: off (voice_volume is 0)")
        return None
    key = elevenlabs_key()
    if key is None:
        log.info(
            "No ElevenLabs key, so Tycho's replies are shown here. Add one: %s", VOICE_KEY_HELP
        )
        return None
    from tycho.voice.elevenlabs import MODEL, ElevenLabsSpeaker

    log.info("Voice: ElevenLabs %s, The Ship's Mind", MODEL)
    from tycho import paths

    return ElevenLabsSpeaker(api_key=key, volume=current.voice_volume, kept=paths.voice_dir())


def load_commands() -> CommandMatcher:
    """The built-in commands plus the player's own file. A broken file is reported
    and skipped, so a typo never stops Tycho starting."""
    try:
        matcher = CommandMatcher.default(paths.commands_file())
    except CommandFileError as e:
        log.warning("Your commands file can't be used, so only the built-in ones are: %s", e)
        return CommandMatcher.default()
    for warning in matcher.warnings:
        log.warning("Your commands file: %s", warning)
    return matcher


def build_session(
    current: Settings,
    binding: Binding,
    store: KeybindStore,
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
) -> Session:
    """The talking loop, wired the way ``tycho run`` wires it."""
    from tycho.game.gamelog import GameLog
    from tycho.input import Presser
    from tycho.lexicon import Lexicon
    from tycho.ptt.monitor import PushToTalk
    from tycho.runtime import Session

    if game_state is None:
        game_log = GameLog(store.install.game_log)
        game_log.facts()  # read the session so far now, not on the first command
        if not game_log.readable:
            log.info("No Game.log yet; Tycho reads it once the game writes one")
        game_state = game_log
    matcher = load_commands()
    return Session(
        audio=audio,
        push_to_talk=PushToTalk(binding, input_sources(binding)),
        transcriber=transcriber,
        lexicon=Lexicon.default(),
        matcher=matcher,
        keybinds=lambda: store.keybinds,
        presser=Presser(sender, focus),
        feedback=feedback,
        game_state=game_state,
        action_log=action_log,
        brain=make_brain(matcher) if make_brain is not None else None,
        speaker=speaker,
        confirm_seconds=current.confirm_seconds,
        log_transcripts=current.keep_transcripts,
        on_result=on_result,
        address=(lambda: address.name) if address is not None else (lambda: ""),
        display=display,
    )


async def serve(session: Session, store: KeybindStore, stop: asyncio.Event | None = None) -> None:
    """Run the session until ``stop``, Ctrl+C, or the console window closing."""
    from tycho.runtime.console import CloseGuard

    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    refresher = asyncio.create_task(_refresh_keybinds(store))

    def request_stop() -> None:
        loop.call_soon_threadsafe(stop.set)

    with CloseGuard(request_stop):
        try:
            await session.run(stop)
        finally:
            refresher.cancel()
            await asyncio.gather(refresher, return_exceptions=True)


async def _refresh_keybinds(store: KeybindStore) -> None:
    last_error: str | None = None
    while True:
        await asyncio.sleep(KEYBIND_REFRESH_SECONDS)
        if await asyncio.to_thread(store.refresh):
            log.info("Keybinds changed in game; using the new ones")
        if store.last_error and store.last_error != last_error:
            log.warning(
                "Couldn't reread keybinds (%s); keeping the last good ones", store.last_error
            )
        last_error = store.last_error


def _download_progress(what: str = "the speech model") -> Callable[[str, int, int], None]:
    """One line per file, updated in place, ending when the file is complete."""
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

    return show


# --- tycho check ---------------------------------------------------------------------

type Level = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    what: str
    detail: str
    fix: str = ""
    """What to run or change to fix it, when there is one (for `tycho check --json`)."""


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

    findings = list(_checks(current, quick))
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


def diagnose(current: Settings, *, quick: bool = False) -> Iterator[Finding]:
    """Each check, printed as it finishes so a slow one doesn't look like a hang."""
    for finding in _checks(current, quick):
        mark = {"ok": " OK ", "warn": "WARN", "fail": "FAIL"}[finding.level]
        print(f"[{mark}] {finding.what}: {finding.detail}")
        yield finding


def _checks(current: Settings, quick: bool, ask: bool = True) -> Iterator[Finding]:
    """``ask``: a missing game folder may be asked for in the terminal. Never from the
    app window, whose thread mustn't wait on a question nobody can see."""
    yield Finding("ok", "Settings", str(paths.settings_file()))
    for name in settings.unknown_keys():
        yield Finding("warn", "Settings", UNKNOWN_SETTING.format(name=name))

    binding = current.push_to_talk_binding
    if binding is None:
        yield Finding(
            "fail", "Push-to-talk", f"not chosen yet. Run: {TYCHO} bind", fix=f"{TYCHO} bind"
        )
    else:
        yield Finding("ok", "Push-to-talk", describe(binding))

    if not quick:
        yield microphone_finding(current)

    if anthropic_key() is None:
        yield Finding(
            "warn",
            "Brain",
            f"no Anthropic key, so only built-in phrasings work: {BRAIN_KEY_HELP}",
            fix=BRAIN_KEY_HELP,
        )
    else:
        yield Finding("ok", "Brain", "Claude, with your Anthropic key")

    if current.voice_volume == 0:
        yield Finding(
            "ok", "Voice", "off (voice_volume is 0); Tycho's replies are shown in its window"
        )
    elif elevenlabs_key() is None:
        yield Finding(
            "warn",
            "Voice",
            f"no ElevenLabs key, so replies are shown in Tycho's window: {VOICE_KEY_HELP}",
            fix=VOICE_KEY_HELP,
        )
    else:
        yield Finding("ok", "Voice", "ElevenLabs, with your key")

    commands_file = paths.commands_file()
    try:
        matcher = CommandMatcher.default(commands_file)
    except CommandFileError as e:
        # A warning, not a failure: run starts anyway, with the built-in commands.
        yield Finding("warn", "Your commands", f"{e} (only the built-in ones are used)")
        matcher = CommandMatcher.default()
    else:
        sequences = sum(c.is_sequence for c in matcher.commands)
        counts = f"{len(matcher.commands) - sequences} commands, {sequences} sequences"
        if commands_file.exists():
            yield Finding("ok", "Your commands", f"{counts}, with yours from {commands_file}")
            for warning in matcher.warnings:
                yield Finding("warn", "Your commands", warning)
        else:
            yield Finding("ok", "Commands", f"{counts}; add your own in {commands_file}")

    from tycho.knowledge.fleet import Fleet

    if problem := Fleet.load(paths.fleet_file()).problem:
        yield Finding("warn", "Your fleet", problem)

    store: KeybindStore | None = None
    try:
        try:
            install = find_install(current)
            yield Finding("ok", "Star Citizen", str(install.folder))
        except FileNotFoundError:
            # Not found anywhere Tycho looks: ask, if someone's there to answer.
            root = ask_game_root(current.game_channel) if ask and sys.stdin.isatty() else None
            if root is None:
                raise
            current = current.model_copy(update={"game_root": root})
            saved = settings.save(current)
            install = find_install(current)
            yield Finding("ok", "Star Citizen", f"{install.folder} (saved to {saved})")
        store = KeybindStore.load(install)
        unbound = [
            c.id
            for c in matcher.commands
            if not c.is_sequence and resolve(c, store.keybinds).action is None
        ]
        detail = f"{len(store.keybinds)} actions"
        if unbound:
            yield Finding(
                "warn",
                "Keybinds",
                f"{detail}; no key for {len(unbound)} voice command(s): {', '.join(unbound)}",
            )
        else:
            yield Finding("ok", "Keybinds", f"{detail}; every voice command has a key")
        log_file = install.game_log
        if log_file.exists():
            yield Finding("ok", "Game.log", str(log_file))
        else:
            yield Finding("warn", "Game.log", f"not found at {log_file}; the game writes it")
    except FileNotFoundError as e:
        yield Finding(
            "fail",
            "Star Citizen",
            str(e),
            fix=f'set game_root = "<the folder holding Data.p4k>" in {paths.settings_file()}',
        )
    except GameDataError as e:
        yield Finding("fail", "Keybinds", f"couldn't read them: {e}")

    if binding is not None and store is not None:
        warnings = conflict_warnings(binding, store)
        if warnings:
            for warning in warnings:
                yield Finding("warn", "Push-to-talk conflict", warning)
        elif binding.kind is DeviceKind.JOYSTICK:
            yield Finding("ok", "Push-to-talk conflict", "joystick buttons aren't checked yet")
        else:
            yield Finding("ok", "Push-to-talk conflict", "not bound to anything in game")

    model = ModelStore(paths.models_dir())
    if model.ready(PARAKEET_V2) or not model.missing(PARAKEET_V2):
        yield Finding("ok", "Speech model", str(model.folder(PARAKEET_V2)))
    else:
        size = PARAKEET_V2.size / 1e9
        yield Finding(
            "warn",
            "Speech model",
            f"not downloaded yet ({size:.1f} GB to {model.folder(PARAKEET_V2)}); "
            f"{MODEL_HELP} (or {TYCHO} run) downloads it. Set TYCHO_MODELS to put it on "
            "another drive.",
            fix=MODEL_HELP,
        )
    yield speech_finding(current)

    if not quick:
        yield from _live_checks(current)


def _live_checks(current: Settings) -> Iterator[Finding]:
    """The checks that ask GitHub or need the player at the PC."""
    if current.check_for_updates:  # the opt-out: no request to GitHub at all
        yield update_finding()
    yield focus_finding()


def start_update_check(current: Settings) -> None:
    """Ask GitHub for a newer Tycho in the background, unless the player turned it off."""
    if current.check_for_updates:
        updates.announce_in_background(__version__)


def update_finding(get: Callable[[str], Any] = updates.latest_json) -> Finding:
    """Whether a newer Tycho is out. Never a problem: at most worth a look."""
    try:
        release = updates.latest_release(get)
    except updates.UpdateError as e:
        return Finding("ok", "Updates", f"couldn't check ({e}); you have {__version__}")
    if release is None:
        return Finding("ok", "Updates", f"no release published yet; you have {__version__}")
    if updates.is_newer(release.version, __version__):
        return Finding(
            "warn", "Updates", f"Tycho {release.version} is out: {release.url}", fix=release.url
        )
    return Finding("ok", "Updates", f"{__version__} is the latest")


def microphone_finding(current: Settings, seconds: float = 2.5) -> Finding:
    from tycho.audio.microphone import Microphone

    try:
        with Microphone(device=microphone_device(current.microphone)) as mic:
            print(f"       Say a few words now ({seconds:.0f} s)...", flush=True)
            mic.start()
            time.sleep(seconds)
            clip = mic.stop()
            name = mic.name
    except AudioError as e:
        return Finding("fail", "Microphone", str(e))
    return microphone_level(name, clip)


def microphone_level(name: str, clip: np.ndarray[tuple[int], np.dtype[np.float32]]) -> Finding:
    peak = float(np.max(np.abs(clip))) if len(clip) else 0.0
    if peak < 0.01:
        return Finding("fail", "Microphone", f"{name} heard nothing. Is it muted?")
    if peak < 0.05:
        return Finding("warn", "Microphone", f"{name} is very quiet; raise its level in Windows")
    if peak > 0.99:
        return Finding("warn", "Microphone", f"{name} is clipping; lower its level in Windows")
    return Finding("ok", "Microphone", f"{name}, level good (peak {20 * np.log10(peak):.0f} dB)")


def focus_finding(seconds: float = 10.0) -> Finding:
    from tycho.input.windows import WindowsFocusCheck, foreground_executable

    focus = WindowsFocusCheck()
    print(f"       Switch to Star Citizen within {seconds:.0f} s to test the focus check...")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if focus.game_has_focus():
            return Finding("ok", "Focus check", "Tycho can tell when Star Citizen is in front")
        time.sleep(0.2)
    front = foreground_executable() or "unknown"
    return Finding(
        "warn",
        "Focus check",
        f"Star Citizen wasn't in front ({front} was). Tycho only presses keys when it is.",
    )


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


def find_install(current: Settings) -> GameInstall:
    roots = search_roots()
    if current.game_root is not None:
        roots = (current.game_root, *roots)
    return GameInstall.find(current.game_channel, roots=roots)


def ask_game_root(channel: str, ask: Callable[[str], str] = input) -> Path | None:
    """Ask where the game is, accepting the channel folder, StarCitizen or Roberts Space
    Industries; None if the player skips or gives up."""
    print(f"       Where is Star Citizen? Paste the folder that contains {channel}, or press")
    print("       Enter to skip. (In the RSI Launcher: Settings, Games, Library Folder.)")
    for _ in range(3):
        try:
            answer = ask("       > ").strip().strip('"')
        except EOFError:
            return None
        if not answer:
            return None
        if (root := root_from(Path(answer), channel)) is not None:
            return root
        print(f"       There's no {channel}\\Data.p4k in or under {answer}. Try again?")
    return None


def device_choice(current: Settings) -> DeviceChoice:
    from tycho.stt import CudaLibraries, choose_device, cuda_available, gpu_free_mb

    if current.transcription_device == "cpu":
        return choose_device("cpu", cuda=False, free_mb=None)
    libraries = CudaLibraries(paths.cuda_dir())
    cuda = cuda_available(libraries.dlls if libraries.ready() else None)
    free_mb = gpu_free_mb() if cuda else None  # no NVIDIA tool runs without CUDA
    return choose_device(current.transcription_device, cuda=cuda, free_mb=free_mb)


def microphone_device(setting: str | None) -> int | str | None:
    """A microphone setting is a device number or (part of) its name."""
    if setting is None or not setting.strip():
        return None
    return int(setting) if setting.strip().isdigit() else setting


def conflict_warnings(binding: Binding, store: KeybindStore) -> list[str]:
    from tycho.ptt import windows as win
    from tycho.ptt.conflicts import conflicts, game_code

    code = game_code(binding, win.scan_code)
    if code is None:
        return []
    names = list(dict.fromkeys(a.label or a.name for a in conflicts(code, store.keybinds)))
    if not names:
        return []
    shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
    return [
        f"{describe(binding)} is also bound in game ({shown}). The game sees the press "
        f"too, so choose another button with `{TYCHO} bind`, or unbind it in game."
    ]


def describe(binding: Binding) -> str:
    from tycho.ptt import windows as win

    if binding.kind is DeviceKind.JOYSTICK:
        return f"joystick button {binding.code + 1}"
    return win.describe(binding)


def input_sources(binding: Binding | None) -> list[InputSource]:
    """Keyboard and mouse always; joysticks when the binding needs them (or to capture one)."""
    from tycho.ptt.windows import KeyboardMouseSource

    sources: list[InputSource] = [KeyboardMouseSource()]
    if binding is None or binding.kind is DeviceKind.JOYSTICK:
        sources.append(_PollThreadJoysticks())
    return sources


class _PollThreadJoysticks:
    """Joysticks, opened on first use: SDL must be set up on the thread that polls it."""

    def __init__(self) -> None:
        self._source: InputSource | None = None

    def _get(self) -> InputSource:
        if self._source is None:
            from tycho.ptt.joystick import JoystickSource

            self._source = JoystickSource()
        return self._source

    def pressed(self) -> frozenset[Binding]:
        return self._get().pressed()

    def is_pressed(self, binding: Binding) -> bool:
        return binding.kind is DeviceKind.JOYSTICK and self._get().is_pressed(binding)

    def close(self) -> None:
        """Let SDL go, on the thread that set it up (a capture from the app window)."""
        source, self._source = self._source, None
        close = getattr(source, "close", None)
        if close is not None:
            close()


def _device_name(device: str) -> str:
    return "graphics card" if device == "gpu" else "processor"


if __name__ == "__main__":
    sys.exit(main())
