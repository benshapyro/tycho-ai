"""Try Tycho's hands in game: type a command, switch to Star Citizen, watch it happen.

    uv run python tools/spike/press.py "lights on"
    uv run python tools/spike/press.py --action v_toggle_landing_system
    uv run python tools/spike/press.py "gear down and request landing" --dry-run

The command goes through the same fast-path matcher Tycho uses, then your own
keybinds. After a countdown, keys are sent only if Star Citizen is the focused
window. This tool refuses dangerous actions outright, and presses nothing when the
settings' game has key actions off (#191). It reads Star Citizen's keybinds, the only
game whose keybinds Tycho can read so far, with the lists of the game the settings pick.
"""

import argparse
import sys
import time

from tycho import settings
from tycho.commands import CommandMatcher, Defer, resolve
from tycho.game.install import GameInstall
from tycho.game.keybinds import Action
from tycho.game.store import KeybindStore
from tycho.input import NotPressable, Press, Presser, UnsupportedKey, plan
from tycho.input.windows import WindowsFocusCheck, WindowsKeySender, foreground_executable
from tycho.packs import as_run, built_in
from tycho.safety import is_confirm_grade


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("say", nargs="?", help='what you would say, e.g. "lights on"')
    parser.add_argument("--action", help="a game action name instead, e.g. v_lights")
    parser.add_argument(
        "--countdown", type=float, default=5.0, help="seconds to switch to the game"
    )
    parser.add_argument("--hold", type=float, default=None, help="seconds to hold a hold action")
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would be pressed, press nothing"
    )
    args = parser.parse_args()
    if not args.say and not args.action:
        parser.error("say a command, or pass --action")

    current = settings.load()
    pack = as_run(built_in(current.game), key_actions=current.key_actions)
    if pack.safety.keys_off:
        print(pack.safety.keys_off)
        return 1
    keybinds = KeybindStore.load(GameInstall.find()).keybinds
    actions: list[Action] = []
    if args.action:
        found = keybinds.get(args.action)
        if found is None:
            print(f"No action called {args.action!r} in your keybinds.")
            return 1
        actions.append(found)
    else:
        result = CommandMatcher.default(pack=pack).match(args.say)
        if isinstance(result, Defer):
            print(
                f"The fast path doesn't handle {args.say!r} ({result.reason}); Claude would decide."
            )
            return 1
        for command in result.commands:
            resolved = resolve(command, keybinds)
            if resolved.action is None:
                print(f"{command.id}: none of {', '.join(command.actions)} has a key bound.")
                return 1
            note = "  (toggle: current state unknown)" if resolved.fallback else ""
            print(f"{command.id} -> {resolved.action.label or resolved.action.name}{note}")
            actions.append(resolved.action)

    presses: list[Press] = []
    for action in actions:
        if is_confirm_grade(action.name, pack.safety):
            name = action.label or action.name
            print(f"Refusing {name}: confirm-grade, and this tool doesn't ask.")
            return 1
        try:
            press = plan(action, keybinds.modes, args.hold)
        except (NotPressable, UnsupportedKey) as e:
            print(e)
            return 1
        keys = " + ".join(f"{k.device}:{k.value:#x}" for k in press.keys)
        print(f"  {action.name}: {keys}, hold {press.hold_seconds:.2f}s, x{press.taps}")
        presses.append(press)

    if args.dry_run:
        print("Dry run: nothing pressed.")
        return 0

    print(f"\nSwitch to {pack.name} now. Pressing in {args.countdown:.0f} seconds...")
    time.sleep(args.countdown)
    presser = Presser(
        WindowsKeySender(), WindowsFocusCheck(pack.window or "StarCitizen.exe"), game=pack.name
    )
    for press in presses:
        receipt = presser.press(press)
        print(
            f"{press.label or press.action}: {receipt.outcome}",
            f"({receipt.detail})" if receipt.detail else "",
        )
        if not receipt.done:
            if receipt.outcome == "no_focus":
                print(f"  The window in front was {foreground_executable() or 'unknown'}.")
            return 1
        time.sleep(0.15)  # a beat between actions in a chain
    return 0


if __name__ == "__main__":
    sys.exit(main())
