"""Build a control profile with Tycho's recommended keybinds, for the player to load in game.

    uv run python tools/spike/make_layout.py            # build, check, save a copy for review
    uv run python tools/spike/make_layout.py --install  # also add it to the game's profiles
    uv run python tools/spike/make_layout.py --restore v_toggle_qdrive_engagement --install

The profile is your current bindings plus keyboard keys for specific on/off
actions (tools/spike/recommended_keybinds.toml). Actions you can already press,
on a key or a mouse button, are left alone. Nothing is removed, except the
keyboard and mouse rebinds of any --restore action, which go back to the game's
defaults. Your live keybinds don't change until you load the profile in the
game's keybinding menu.
"""

import argparse
import sys
import tomllib
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from tycho.game.install import GameInstall
from tycho.game.keybinds import Keybinds, KeyInput, apply_profile
from tycho.game.layout import Addition, LayoutError, already_bound, build, outstanding, write
from tycho.game.store import KeybindStore

HERE = Path(__file__).resolve().parent
REVIEW = HERE.parents[1] / "recordings" / "layouts"  # gitignored


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--plan", type=Path, default=HERE / "recommended_keybinds.toml")
    parser.add_argument("--name", default=f"Tycho_{date.today():%Y-%m-%d}")
    parser.add_argument(
        "--install", action="store_true", help="put it in the game's profiles folder"
    )
    parser.add_argument(
        "--restore",
        action="append",
        default=[],
        metavar="ACTION",
        help="give this action back its default keyboard key and mouse button",
    )
    args = parser.parse_args()

    install = GameInstall.find()
    store = KeybindStore.load(install)
    entries = tomllib.loads(args.plan.read_text("utf-8-sig"))["add"]
    planned = [Addition(str(e["action"]), str(e["key"])) for e in entries]
    additions = outstanding(planned, store.keybinds)
    if done := len(planned) - len(additions):
        print(f"{done} of the {len(planned)} recommended bindings are already in your layout.")
    for skipped in already_bound(additions, store.keybinds):
        action = store.keybinds.get(skipped.action)
        on = ", ".join(map(str, action.inputs)) if action else "?"
        print(f"Leaving {skipped.action} on {on}, as you have it.")
    taken = set(already_bound(additions, store.keybinds))
    additions = [a for a in additions if a not in taken]
    if not additions and not args.restore:
        print("Nothing to add.")
        return 0
    profile = ET.parse(install.actionmaps).getroot()  # noqa: S314 - the player's own file
    try:
        root = build(
            profile,
            additions,
            list(store.keybinds),
            args.name,
            "Tycho recommended keybinds",
            restore=args.restore,
        )
    except LayoutError as e:
        print(f"Can't build the profile: {e}")
        return 1

    # Read the new profile back with Tycho's own reader, the way the game would apply it.
    after = Keybinds(apply_profile(store.defaults, root), store.keybinds.modes)
    added = {a.action for a in additions} | set(args.restore)
    print(f"{len(additions)} bindings added; each one reads back as:")
    for addition in additions:
        action = after.get(addition.action)
        expected = str(KeyInput.parse(addition.key))
        keys = [str(i) for i in action.inputs] if action else []
        ok = expected in keys
        label = action.label if action else addition.action
        print(f"  {'ok ' if ok else 'BAD'} {expected:11} {label}")
        if not ok:
            return 1
    for name in args.restore:
        restored = after.get(name)
        keys = ", ".join(map(str, restored.inputs)) if restored else "nothing"
        print(f"  restored {name} to the game default: {keys or 'unbound'}")
    # Compare per control context: the same action can appear in several actionmaps.
    now = {(a.actionmap, a.name): sorted(map(str, a.inputs)) for a in after}
    changed = [
        f"{before.actionmap}/{before.name}"
        for before in store.keybinds
        if before.name not in added
        and sorted(map(str, before.inputs)) != now.get((before.actionmap, before.name))
    ]
    if changed:
        print(f"Other bindings changed unexpectedly: {sorted(set(changed))}")
        return 1
    print(f"Every other binding is unchanged ({len(store.keybinds) - len(additions)} checked).")

    review_path = REVIEW / f"layout_{args.name}_exported.xml"
    if not review_path.exists():
        print(f"\nReview copy: {write(root, REVIEW)}")
    if args.install:
        path = write(root, install.control_profiles)
        print(f"Installed: {path}")
        print("Load it in game from the keybindings menu's control profiles.")
    else:
        print("Run again with --install to put it in the game's profiles folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
