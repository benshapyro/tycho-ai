"""Every ``tycho check`` finding, printing nothing (#61, ADR 19). The command line
prints them (``tycho check``, ``--json``); the app window shows them.

A game's own findings come from ``tycho.games`` (Star Citizen's folder, keybinds and
Game.log); the rest are Tycho's: settings, push-to-talk, the microphone, speech
recognition, the brain, the voice and updates.
"""

import logging
import sys
import time
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np

from tycho import __version__, crashes, paths, settings, updates
from tycho.audio.source import AudioError, microphone_device
from tycho.brain_choice import brain_findings
from tycho.commands import CommandFileError, CommandMatcher
from tycho.findings import TYCHO, Finding, key_finding
from tycho.games import fleet_findings, game_findings
from tycho.packs import YOU_TURNED_THEM_OFF, GameKeys, GamePack, PackError, as_run, built_in
from tycho.ptt.binding import DeviceKind
from tycho.ptt.conflicts import conflict_warnings
from tycho.ptt.devices import describe
from tycho.settings import Settings
from tycho.stt import PARAKEET_V2, ModelStore, download_size, speech_models
from tycho.stt.choice import GPU_HELP, MODEL_HELP, device_choice, device_name, gpu_hint
from tycho.voice import choice as voice_choice
from tycho.voice.choice import VOICE_KEY_HELP, choose_voice, elevenlabs_key

log = logging.getLogger("tycho")


UNKNOWN_SETTING = (
    "{name} in settings.toml isn't a setting this Tycho knows, so it's ignored. It may be a "
    "typo, or from a newer Tycho."
)


def key_actions_finding(pack: GamePack) -> Finding:
    """What ``tycho check`` and the window's status say about the game's mode (#191)."""
    if not pack.safety.keys_off:
        return Finding("ok", "Key actions", f"on in {pack.name}: Tycho presses your keys")
    fix = f"key_actions in {paths.settings_file()}" if pack.why == YOU_TURNED_THEM_OFF else ""
    return Finding(
        "ok",
        "Key actions",
        f"off in {pack.name}, so Tycho answers, looks things up and shows cards, and "
        f"presses nothing. {pack.why}",
        fix=fix,
    )


def speech_finding(current: Settings) -> Finding:
    """Where speech recognition runs, and how to move it to the graphics card if it
    could be there (audit, 2026-11-03: check warned without saying what to do)."""
    from tycho.stt import nvidia_driver

    choice = device_choice(current)
    detail = f"on the {device_name(choice.device)}: {choice.reason}"
    if choice.device == "gpu" or current.transcription_device != "gpu":
        return Finding("ok", "Speech recognition", detail)
    if hint := gpu_hint():
        fix = GPU_HELP if hint.endswith(GPU_HELP) else ""
        return Finding("warn", "Speech recognition", f"{detail}. {hint}", fix=fix)
    if nvidia_driver() is None:  # nothing to do about it: fine, just slower
        return Finding("ok", "Speech recognition", "on the processor (no NVIDIA card)")
    return Finding("warn", "Speech recognition", detail)


def diagnose(current: Settings, *, quick: bool = False) -> Iterator[Finding]:
    """Each check, printed as it finishes so a slow one doesn't look like a hang."""
    for finding in checks(current, quick):
        mark = {"ok": " OK ", "warn": "WARN", "fail": "FAIL"}[finding.level]
        print(f"[{mark}] {finding.what}: {finding.detail}")
        yield finding


def checks(current: Settings, quick: bool, ask: bool = True) -> Iterator[Finding]:
    """``ask``: a missing game folder may be asked for in the terminal. Never from the
    app window, whose thread mustn't wait on a question nobody can see."""
    yield Finding("ok", "Settings", str(paths.settings_file()))
    for name in settings.unknown_keys():
        yield Finding("warn", "Settings", UNKNOWN_SETTING.format(name=name))
    try:
        pack = as_run(built_in(current.game), key_actions=current.key_actions)
    except PackError as e:
        yield Finding("fail", "Game", str(e))
        return
    yield Finding("ok", "Game", f"{pack.name} (game pack {pack.version})")
    yield key_actions_finding(pack)

    binding = current.push_to_talk_binding
    if binding is None:
        yield Finding(
            "fail", "Push-to-talk", f"not chosen yet. Run: {TYCHO} bind", fix=f"{TYCHO} bind"
        )
    else:
        yield Finding("ok", "Push-to-talk", describe(binding))

    if not quick:
        yield microphone_finding(current)

    yield from brain_findings(current, quick=quick)

    if current.voice_volume == 0:
        yield Finding(
            "ok", "Voice", "off (voice_volume is 0); Tycho's replies are shown in its window"
        )
    elif elevenlabs_key() is None:
        yield key_finding(
            "elevenlabs",
            "Voice",
            "ElevenLabs",
            "replies are shown in Tycho's window",
            VOICE_KEY_HELP,
        )
    else:
        yield voice_finding(current, quick)

    commands_file = paths.commands_file(pack.id)
    try:
        matcher = CommandMatcher.default(commands_file, pack=pack)
    except CommandFileError as e:
        # A warning, not a failure: run starts anyway, with the built-in commands.
        yield Finding("warn", "Your commands", f"{e} (only the built-in ones are used)")
        matcher = CommandMatcher.default(pack=pack)
    else:
        sequences = sum(c.is_sequence for c in matcher.commands)
        counts = f"{len(matcher.commands) - sequences} commands, {sequences} sequences"
        if commands_file.exists():
            yield Finding("ok", "Your commands", f"{counts}, with yours from {commands_file}")
            for warning in matcher.warnings:
                yield Finding("warn", "Your commands", warning)
        else:
            yield Finding("ok", "Commands", f"{counts}; add your own in {commands_file}")

    if "fleet" in pack.parts.lookups:  # #413, as #339: no fleet file for a game without one
        yield from fleet_findings(pack)

    store: GameKeys | None = None
    # A pack with no keybinds part (an advisor, #191) has no game folder or keys to check.
    if pack.parts.keybinds is not None:
        store = yield from game_findings(current, matcher, ask=ask and sys.stdin.isatty())

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
    specs = speech_models(device_choice(current).device)
    if model.has(specs):
        yield Finding("ok", "Speech model", str(model.folder(PARAKEET_V2)))
    else:
        size = download_size(specs) / 1e9
        yield Finding(
            "warn",
            "Speech model",
            f"not downloaded yet ({size:.1f} GB to {model.folder(PARAKEET_V2)}); "
            f"{MODEL_HELP} (or {TYCHO} run) downloads it. Set TYCHO_MODELS to put it on "
            "another drive.",
            fix=MODEL_HELP,
        )
    yield speech_finding(current)
    if crashed := crashes.finding():  # a native crash in the last few days (#296)
        yield crashed

    if not quick:
        yield from live_checks(current, pack)


def voice_finding(current: Settings, quick: bool) -> Finding:
    """The voice and model Tycho will speak with. ``tycho check`` asks ElevenLabs
    whether the key can use it (free); the window's quick checks show what the running
    Tycho chose at start instead of asking again."""
    from tycho.voice import voices

    if quick:
        if voice_choice.voice_now is not None:
            return voice_choice.voice_now
        chosen = voices.choose(current.voice, current.voice_model)
        return Finding("ok", "Voice", f"ElevenLabs, {chosen.describe()}, with your key")
    key = elevenlabs_key()
    if key is None:  # removed since the check above
        return Finding("warn", "Voice", f"no ElevenLabs key: {VOICE_KEY_HELP}", fix=VOICE_KEY_HELP)
    return choose_voice(current, key)[1]


def live_checks(current: Settings, pack: GamePack) -> Iterator[Finding]:
    """The checks that ask GitHub or need the player at the PC. The focus check is only
    for a game Tycho presses keys in: an advisor game has no window to be in front."""
    if current.check_for_updates:  # the opt-out: no request to GitHub at all
        yield update_finding()
    if pack.window and not pack.safety.keys_off:
        yield focus_finding(pack.name, pack.window)


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
    from tycho.audio.clip import loudness

    peak, how = loudness(clip)
    if how == "silent":
        return Finding("fail", "Microphone", f"{name} heard nothing. Is it muted?")
    if how == "quiet":
        return Finding("warn", "Microphone", f"{name} is very quiet; raise its level in Windows")
    if how == "clipping":
        return Finding("warn", "Microphone", f"{name} is clipping; lower its level in Windows")
    return Finding("ok", "Microphone", f"{name}, level good (peak {20 * np.log10(peak):.0f} dB)")


def focus_finding(game: str, window: str, seconds: float = 10.0) -> Finding:
    """Whether Tycho can tell that ``game`` (the pack's name, never a game named here:
    docs/voice-and-tone.md) is in front. ``window`` is the pack's program."""
    from tycho.input.windows import WindowsFocusCheck, foreground_executable

    focus = WindowsFocusCheck(window)
    print(f"       Switch to {game} within {seconds:.0f} s to test the focus check...")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if focus.game_has_focus():
            return Finding("ok", "Focus check", f"Tycho can tell when {game} is in front")
        time.sleep(0.2)
    front = foreground_executable()
    # Name the program in front only when Tycho can tell which it was (#430).
    instead = f" ({front} was)" if front else ""
    return Finding(
        "warn",
        "Focus check",
        f"{game} wasn't in front{instead}. Tycho only presses keys when it is: "
        f"start the game, then run the check again.",
    )
