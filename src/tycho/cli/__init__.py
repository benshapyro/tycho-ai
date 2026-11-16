"""The ``tycho`` command (ADR 11): the parser and which command runs. The commands are in
``cli.commands``; ``tycho run`` is ``tycho.start``. Only ``__main__`` and the packaged
app import this (ADR 19).
"""

import argparse
import sys

from tycho import __version__, credentials, settings
from tycho.cli.commands import (
    actions_command,
    check_command,
    check_json,
    credits_command,
    gpu_command,
    model_command,
    sounds_command,
)
from tycho.doctor import UNKNOWN_SETTING
from tycho.settings import Settings, SettingsError
from tycho.start.run import bind_command, run_command


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
            return model_command(args.action, current)
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
