"""``tycho run``: setup if it's needed, then start, listen, and start again when the
window picks a new game. Out of ``app.py`` (#398)."""

import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tycho import __version__, crashes, logs, settings
from tycho.audio.source import AudioError, microphone_device
from tycho.brain_choice import load_brain
from tycho.desktop import shell
from tycho.doctor import start_update_check
from tycho.findings import TYCHO
from tycho.packs import GameDataError, built_in
from tycho.ptt.devices import describe, input_sources
from tycho.settings import Settings, SettingsError
from tycho.start.assemble import build_session, game_pack, load_keybinds, load_transcriber
from tycho.start.serve import serve
from tycho.stt import ModelError
from tycho.voice.choice import load_speaker

if TYPE_CHECKING:
    from tycho.runtime.instance import OpenRequests
    from tycho.spoken_settings import Live
    from tycho.tile.display import TileDisplay

log = logging.getLogger("tycho")


def _float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"expected a number, not {value!r}")
    return float(value)


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
    # A native crash writes what Tycho was doing to a file in the logs folder (#296);
    # this start finds the last run's, if it crashed, and says so.
    crashed = crashes.watch(log_file.parent)
    start_update_check(current)
    if window and sys.platform == "win32":
        from tycho.desktop.setup import needs_setup

        if needs_setup(current):
            # A first run (no push-to-talk, or no game): the window asks, not the terminal.
            log.info("Opening Tycho's window to set up")
            outcome = shell.first_run(requests)
            if outcome == "closed":
                print("Setup isn't finished, so Tycho isn't listening. Run it again to finish.")
                return 1
            if outcome == "no_window" and WINDOWED:
                print(shell.NO_WINDOW, file=sys.stderr)
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
    if crashed is not None:
        notices.append(crashes.STOPPED.format(file=crashed))
        log.warning("%s", notices[0])
    while True:
        starting = shell.Starting(requests) if window and sys.platform == "win32" else None
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
                if shell.first_run(requests) == "done":
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
    starting: shell.Starting | None,
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
        tile.on_stopped = lambda _why: session.tell(TILE_STOPPED)  # said, once (#297)
        log.info("Listening. Hold %s and speak; press Ctrl+C to stop.", describe(binding))
        if starting is not None:
            starting.close()  # the tray's own star takes its place
        restart = False
        try:
            if window and sys.platform == "win32":
                outcome = shell.serve_with_tray(
                    session,
                    live,
                    display,
                    requests,
                    serve=lambda stop: serve(session, store, stop),
                    reopen=reopen,
                    windowed=WINDOWED,
                )
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


TILE_STOPPED = (
    "The Tile stopped working, so Tycho says its answers and shows no cards. Starting "
    "Tycho again brings it back."
)
"""Said when the Tile's window stops while Tycho runs (#297); Tycho keeps listening."""


NO_BUTTON = (
    "Tycho has no push-to-talk button yet. Open Tycho and choose one in setup, or run "
    "`tycho bind` in a terminal."
)


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
