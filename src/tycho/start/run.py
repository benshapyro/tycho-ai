"""``tycho run``: setup if it's needed, then start, listen, and start again when the
window picks a new game. Out of ``app.py`` (#398)."""

import asyncio
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tycho import __version__, crash_reports, crashes, logs, settings
from tycho.audio.source import AudioError, microphone_device
from tycho.brain_choice import load_brain
from tycho.desktop import shell
from tycho.doctor import start_update_check
from tycho.findings import TYCHO
from tycho.history import follow, kept
from tycho.packs import GameDataError, built_in
from tycho.ptt.binding import every_binding
from tycho.ptt.devices import describe, describe_all, input_sources
from tycho.runtime.messages import Result
from tycho.runtime.recent import Recent
from tycho.settings import Settings, SettingsError
from tycho.start.assemble import (
    build_session,
    game_pack,
    game_state_for,
    load_keybinds,
    load_transcriber,
    watch_for_callouts,
)
from tycho.start.serve import serve
from tycho.stt import ModelError
from tycho.voice.choice import load_speaker

if TYPE_CHECKING:
    from tycho.runtime.instance import OpenRequests
    from tycho.runtime.ports import Speaker
    from tycho.spoken_settings import Live
    from tycho.tile.display import TileDisplay

log = logging.getLogger("tycho")


def _float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"expected a number, not {value!r}")
    return float(value)


def _ducking(speaker: Speaker, program: str, current: Settings, live: Live) -> Speaker:
    """``speaker``, turning ``program``'s sound down while it speaks (#570), with a level
    a crash left lowered put back first."""
    from tycho import paths
    from tycho.voice.ducking import Ducker, Ducking
    from tycho.voice.mixer import WindowsGameVolume

    ducker = Ducker(
        WindowsGameVolume(program),
        paths.ducked_file(),
        on=current.duck_game,
        level=current.duck_level,
    )
    ducker.recover()
    live["duck_game"] = ducker.set_on
    live["duck_level"] = ducker.set_level
    return Ducking(speaker, ducker)


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
            _crash_reports(current)  # one Tycho sends what waits, never two at once (#429)
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


def _crash_reports(current: Settings) -> None:
    """Crash reports for this run, only if the player said yes (ADR 4, #199)."""

    def allowed() -> bool:
        try:
            return settings.load().crash_reports is True
        except SettingsError:
            return False

    context = crash_reports.Context(pack=current.game, provider=current.brain_provider)
    crash_reports.start(
        current.crash_reports, allowed, context, (current.call_me, current.call_me_says)
    )


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
    try_it = False
    """A first run was set up in the window: it opens again on setup's Try it (ADR 17)."""
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
            try_it = outcome == "done"
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
                try_it=try_it,
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
        reopen, try_it = True, False
        if previous.game != current.game and window and sys.platform == "win32":
            from tycho.desktop.setup import needs_setup

            if needs_setup(current):
                # The new game needs setting up (its folder isn't found): the window asks.
                log.info("Opening Tycho's window to set up %s", current.game)
                if shell.first_run(requests) == "done":
                    # Set up like a first run, so the window opens on Try it (#467).
                    current, try_it = settings.load(), True
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
    try_it: bool = False,
) -> int:
    """Everything after setup: the speech model, the keybinds, the Tile, the
    microphone, then listening. ``RESTART`` if the window asked to start again;
    ``reopen`` opens the window once listening, as it was before a restart. ``notices``
    are shown on the Tile once it's up (why a new game didn't start, #329). ``try_it``
    opens the window once listening, on setup's Try it (ADR 17)."""
    from tycho.address import Address, Pronouncing
    from tycho.audio.microphone import Microphone
    from tycho.eyes.windows import WindowsCamera
    from tycho.feedback import SoundDevicePlayer, SoundFeedback
    from tycho.input.windows import GAME_EXECUTABLE, WindowsFocusCheck, WindowsKeySender
    from tycho.runtime.actionlog import FileActionLog
    from tycho.tile import record as tile_record
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
    bindings = every_binding(current.push_to_talk, current.push_to_talk_2)

    pack = game_pack(current)
    if starting is not None:
        starting.doing("Reading your keybinds")
    # A pack with no keybinds part (an advisor, #191) has none to read, and no game folder.
    store = load_keybinds(current, bindings) if pack.parts.keybinds is not None else None
    transcriber = load_transcriber(current, starting.doing if starting is not None else None)
    action_log = FileActionLog()
    log.info("Action log: %s", action_log.path)
    feedback = SoundFeedback(SoundDevicePlayer(), current.feedback_volume)
    told: list[str] = list(notices)
    """The voice fell back to George (#242), a game didn't start, or the last run
    crashed (#296): shown on the Tile once it's up."""

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
    if speaker is not None and pack.window and sys.platform == "win32":
        speaker = _ducking(speaker, pack.window, current, live)  # the game down (#570)
    if isinstance(voice, ElevenLabsSpeaker):
        live["voice_volume"] = lambda v: voice.set_volume(_float(v))
    for name in ("call_me", "call_me_says", "call_me_how_often"):
        live[name] = lambda v, name=name: address.set(name, v)
    # The Tile (ADR 16): a window of its own, never anything in the game.
    tile = TileDisplay(current, record=tile_record.from_env())  # a review tool, off
    unseen = told
    """What the Tile couldn't show (it's off, or didn't open): said aloud instead (#428)."""
    if tile.start():
        display = tile
        unseen = [said for said in told if not tile.show(notice(said))]
        for name in Settings.model_fields:
            if name == "tile" or name.startswith("tile_"):
                live[name] = lambda v, name=name: tile.change(name, v)
    recent = Recent()  # Home's last exchanges: memory only (ADR 17)
    history = kept(current.keep_history is True)  # on the PC, only if they said yes (#484)
    follow(live, history)

    def ended(result: Result) -> None:
        recent.add(result)
        history.record(result)

    with Microphone(device=microphone_device(current.microphone)) as mic:
        log.info("Microphone: %s", mic.name)
        game_state = game_state_for(pack, store)
        session = build_session(
            current,
            bindings,
            store,
            transcriber,
            game_state=game_state,
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
            camera=WindowsCamera(pack.window) if pack.window and pack.look else None,
            pack=pack,
            on_result=ended,
        )
        live["confirm_seconds"] = lambda v: session.set_confirm_seconds(_float(v))
        live["look_at_screen"] = lambda v: session.set_looking(v is True)
        callouts = watch_for_callouts(current, pack, game_state, session)
        if callouts is not None:
            live["callouts"] = callouts.turn_on
        tile.on_stopped = lambda _why: session.tell(TILE_STOPPED)  # said, once (#297)
        for said in unseen:  # the Tile is off or didn't open: said aloud once listening
            session.tell(said)
        log.info("Listening. Hold %s and speak; press Ctrl+C to stop.", describe_all(bindings))
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
                    serve=lambda stop: serve(session, store, stop, callouts),
                    reopen=reopen,
                    windowed=WINDOWED,
                    recent=recent.exchanges,
                    try_it=try_it,
                )
                if outcome == "failed":
                    return 1
                restart = outcome == "restart"
            else:
                asyncio.run(serve(session, store, callouts=callouts))
        finally:
            action_log.close()
            history.close()
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


def bind_command(current: Settings, *, second: bool = False) -> Settings | None:
    """Choose the push-to-talk button, or with ``second`` another one held the same way
    (#621): pressing the first button then isn't taken as the second."""
    from tycho.ptt import windows as win
    from tycho.ptt.monitor import capture_binding

    ignore = {win.LEFT_MOUSE}
    if second:
        first = current.push_to_talk_binding
        if first is None:
            print("Choose your first push-to-talk button before a second: run `tycho bind`.")
            return None
        ignore.add(first)
        print("Press the second button you want to hold while talking, say one on your")
        print(f"joystick. {describe(first)} stays your push-to-talk too.")
    else:
        print("Press the button you want to hold while talking: a keyboard key, a mouse")
        print("button (not left click) or a joystick button. A mouse side button works well.")
    sources = input_sources(None)
    found = capture_binding(sources, ignore=frozenset(ignore), timeout=30)
    if found is None:
        print("No button pressed within 30 seconds; nothing changed.")
        return None
    name = "push_to_talk_2" if second else "push_to_talk"
    changes: dict[str, object] = {name: str(found)}
    if not second and current.push_to_talk_2 == str(found):
        changes["push_to_talk_2"] = None  # the same button twice is one button
    updated = current.model_copy(update=changes)
    path = settings.save(updated)
    which = "Your second push-to-talk button" if second else "Push-to-talk"
    print(f"{which} is now {describe(found)}. Saved to {path}")
    return updated


def remove_second_command(current: Settings) -> int:
    """Back to one push-to-talk button."""
    if current.push_to_talk_2 is None:
        print("You have one push-to-talk button already; nothing changed.")
        return 0
    path = settings.save(current.model_copy(update={"push_to_talk_2": None}))
    print(f"Removed your second push-to-talk button. Saved to {path}")
    return 0
