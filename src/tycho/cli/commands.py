"""The ``tycho`` commands other than ``run``: ``check``, ``actions``, ``model``, ``gpu``,
``sounds`` and ``credits``. Out of ``app.py`` (#398)."""

import logging
import sys
import time
from pathlib import Path

from tycho import __version__, doctor, paths
from tycho.commands import CommandFileError, CommandMatcher
from tycho.doctor import diagnose
from tycho.findings import TYCHO
from tycho.games import find_install, load_keys
from tycho.packs import GameDataError, built_in
from tycho.settings import Settings
from tycho.start.assemble import download_progress
from tycho.stt import PARAKEET_V2, ModelError, ModelStore
from tycho.stt.choice import GPU_HELP, MODEL_HELP

log = logging.getLogger("tycho")


def notice_file() -> Path:
    """NOTICE: the credits and the unofficial-fan-project notice. The installed app has
    it beside tycho.exe (tools/notices.py puts it there); in development, the repo's."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("NOTICE.txt")
    return Path(__file__).resolve().parents[3] / "NOTICE"


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
        model.ensure(PARAKEET_V2, download_progress())
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
        libraries.ensure(download_progress("the graphics card libraries"))
    except ModelError as e:
        print(e, file=sys.stderr)
        return 2
    print("Done. Speech recognition will use the graphics card the next time Tycho starts.")
    return 0


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
