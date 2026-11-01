"""The ``tycho`` command: run the talking loop, check the setup, choose a button (ADR 11)."""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

from tycho import __version__, logs, paths, settings
from tycho.audio.source import AudioError
from tycho.commands import CommandFileError, CommandMatcher, resolve
from tycho.game.install import GameInstall, search_roots
from tycho.game.store import GameDataError, KeybindStore
from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.monitor import InputSource
from tycho.settings import Settings, SettingsError
from tycho.stt import PARAKEET_V2, DeviceChoice, ModelError, ModelStore

if TYPE_CHECKING:  # the runtime pulls in the hands and audio; imported when running
    from tycho.audio.source import AudioSource
    from tycho.input import FocusCheck, KeySender
    from tycho.runtime import ActionLog, Feedback, GameState, Result, Session, Transcriber
    from tycho.stt.parakeet import ParakeetTranscriber

log = logging.getLogger("tycho")

KEYBIND_REFRESH_SECONDS = 2.0
"""How often to look for keybinds the player changed in game."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tycho", description="Tycho, an unofficial voice co-pilot for Star Citizen."
    )
    parser.add_argument("--version", action="version", version=f"Tycho {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    run = commands.add_parser("run", help="start listening (the default)")
    run.add_argument("-v", "--verbose", action="store_true", help="show debug detail")
    check = commands.add_parser("check", help="check the setup, in plain words")
    check.add_argument("--quick", action="store_true", help="skip the microphone and focus tests")
    commands.add_parser("bind", help="choose the push-to-talk button")
    commands.add_parser("sounds", help="play the done, not-done and asking sounds")
    args = parser.parse_args(argv)

    try:
        current = settings.load()
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    match args.command:
        case "check":
            return check_command(current, quick=args.quick)
        case "bind":
            return 0 if bind_command(current) else 1
        case "sounds":
            return sounds_command(current)
        case _:
            return run_command(current, verbose=getattr(args, "verbose", False))


# --- tycho run -----------------------------------------------------------------------


def run_command(current: Settings, *, verbose: bool = False) -> int:
    from tycho.runtime import AlreadyRunning, SingleInstance

    log_file = logs.setup(verbose=verbose)
    try:
        with SingleInstance():
            return _run(current, log_file)
    except AlreadyRunning as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except (FileNotFoundError, GameDataError, AudioError, ModelError, SettingsError) as e:
        log.error("%s", e)
        print("Run `tycho check` for a full diagnosis.", file=sys.stderr)
        return 1


def _run(current: Settings, log_file: Path) -> int:
    from tycho.audio.microphone import Microphone
    from tycho.feedback import SoundDevicePlayer, SoundFeedback
    from tycho.input.windows import WindowsFocusCheck, WindowsKeySender
    from tycho.runtime.actionlog import FileActionLog

    log.info("Tycho %s. Log file: %s", __version__, log_file)
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
            feedback=SoundFeedback(SoundDevicePlayer(), current.feedback_volume),
            action_log=action_log,
        )
        log.info("Listening. Hold %s and speak; press Ctrl+C to stop.", describe(binding))
        try:
            asyncio.run(serve(session, store))
        finally:
            action_log.close()
    if mic.overflows:
        log.warning("The microphone dropped audio %d time(s)", mic.overflows)
    return 0


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
    return transcriber


def load_commands() -> CommandMatcher:
    """The built-in commands plus the player's own file. A broken file is reported
    and skipped, so a typo never stops Tycho starting."""
    try:
        return CommandMatcher.default(paths.commands_file())
    except CommandFileError as e:
        log.warning("Your commands file can't be used, so only the built-in ones are: %s", e)
        return CommandMatcher.default()


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
    on_result: Callable[[Result], None] | None = None,
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
    return Session(
        audio=audio,
        push_to_talk=PushToTalk(binding, input_sources(binding)),
        transcriber=transcriber,
        lexicon=Lexicon.default(),
        matcher=load_commands(),
        keybinds=lambda: store.keybinds,
        presser=Presser(sender, focus),
        feedback=feedback,
        game_state=game_state,
        action_log=action_log,
        confirm_seconds=current.confirm_seconds,
        log_transcripts=current.keep_transcripts,
        on_result=on_result,
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


def _download_progress() -> Callable[[str, int, int], None]:
    """One line per file, updated in place, ending when the file is complete."""
    shown: dict[str, int] = {}

    def show(name: str, done: int, total: int) -> None:
        percent = 100 * done // total if total else 0
        if shown.get(name) == percent:
            return
        shown[name] = percent
        size = f"{done / 1e9:.2f} of {total / 1e9:.2f} GB" if total > 1e8 else ""
        end = "\n" if done >= total else ""
        line = f"  Downloading the speech model: {name} {percent:3d}% {size}"
        print("\r" + line, end=end, flush=True)  # \r: overwrite the line in place

    return show


# --- tycho check ---------------------------------------------------------------------

type Level = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    what: str
    detail: str


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
        print("All good. Start Tycho with: tycho run")
    return 1 if failed else 0


def diagnose(current: Settings, *, quick: bool = False) -> Iterator[Finding]:
    """Each check, printed as it finishes so a slow one doesn't look like a hang."""
    for finding in _checks(current, quick):
        mark = {"ok": " OK ", "warn": "WARN", "fail": "FAIL"}[finding.level]
        print(f"[{mark}] {finding.what}: {finding.detail}")
        yield finding


def _checks(current: Settings, quick: bool) -> Iterator[Finding]:
    yield Finding("ok", "Settings", str(paths.settings_file()))

    binding = current.push_to_talk_binding
    if binding is None:
        yield Finding("fail", "Push-to-talk", "not chosen yet. Run: tycho bind")
    else:
        yield Finding("ok", "Push-to-talk", describe(binding))

    if not quick:
        yield microphone_finding(current)

    commands_file = paths.commands_file()
    try:
        matcher = CommandMatcher.default(commands_file)
    except CommandFileError as e:
        yield Finding("fail", "Your commands", f"{e} (only the built-in ones are used)")
        matcher = CommandMatcher.default()
    else:
        sequences = sum(c.is_sequence for c in matcher.commands)
        counts = f"{len(matcher.commands) - sequences} commands, {sequences} sequences"
        if commands_file.exists():
            yield Finding("ok", "Your commands", f"{counts}, with yours from {commands_file}")
        else:
            yield Finding("ok", "Commands", f"{counts}; add your own in {commands_file}")

    store: KeybindStore | None = None
    try:
        install = find_install(current)
        yield Finding("ok", "Star Citizen", str(install.folder))
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
        yield Finding("fail", "Star Citizen", str(e))
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
            "tycho run downloads it. Set TYCHO_MODELS to put it on another drive.",
        )
    choice = device_choice(current)
    yield Finding(
        "ok" if choice.device == "gpu" or current.transcription_device == "cpu" else "warn",
        "Speech recognition",
        f"on the {_device_name(choice.device)}: {choice.reason}",
    )

    if not quick:
        yield focus_finding()


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
    return GameInstall.find(roots=roots)


def device_choice(current: Settings) -> DeviceChoice:
    from tycho.stt import choose_device, cuda_available, gpu_free_mb

    if current.transcription_device == "cpu":
        return choose_device("cpu", cuda=False, free_mb=None)
    return choose_device(current.transcription_device, cuda=cuda_available(), free_mb=gpu_free_mb())


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
        "too, so choose another button with `tycho bind`, or unbind it in game."
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


def _device_name(device: str) -> str:
    return "graphics card" if device == "gpu" else "processor"


if __name__ == "__main__":
    sys.exit(main())
