"""Building control profiles the player imports through the game's own menu.

Tycho never edits the live actionmaps.xml: the game rewrites it while running,
and the player should review changes first. Instead it writes a new, separately
named profile in the game's export format (as written by "Save control
settings"), which the player loads in game when they choose.

A profile starts from the player's current bindings and only adds keyboard
bindings for actions that have no keyboard key or mouse button, on key
combinations nothing else uses. The game keeps keyboard and mouse in one slot
per action, so adding a key to an action on a mouse button would take the
button away.
"""

import copy
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tycho.game.keybinds import Action, KeyInput

_JOYSTICK = re.compile(r"^js(\d+)_")


class LayoutError(ValueError):
    """A requested binding can't be added safely."""


@dataclass(frozen=True, slots=True)
class Addition:
    action: str
    key: str
    """In the game's notation, e.g. "rctrl+l"."""


def used_keys(actions: Iterable[Action]) -> dict[str, list[str]]:
    """Every keyboard combination in use, and the actions using it, across all contexts."""
    used: dict[str, list[str]] = {}
    for action in actions:
        for key in action.inputs:
            if key.device == "keyboard":
                used.setdefault(str(key), []).append(action.name)
    return used


def already_bound(additions: Iterable[Addition], actions: Iterable[Action]) -> list[Addition]:
    """Additions for actions the player can already press, which Tycho leaves alone.

    Pass the result's complement to ``build``; these are the ones to report as skipped.
    """
    pressable = {a.name for a in actions if a.inputs}
    return [addition for addition in additions if addition.action in pressable]


def outstanding(additions: Iterable[Addition], actions: Iterable[Action]) -> list[Addition]:
    """The additions not already in place, so a grown list can be applied again safely."""
    bound = {(a.name, str(i)) for a in actions for i in a.inputs}
    return [
        addition
        for addition in additions
        if (key := KeyInput.parse(addition.key)) is None or (addition.action, str(key)) not in bound
    ]


_WINDOWS_SHORTCUTS = {
    ("alt", "f4"): "closes the game",
    ("alt", "tab"): "switches windows",
    ("alt", "escape"): "switches windows",
    ("alt", "space"): "opens the window menu",
    ("alt", "enter"): "toggles full screen",
    ("ctrl", "escape"): "opens the Start menu",
}


def reserved_by_windows(key: KeyInput) -> str | None:
    """What Windows does with this combination, if it takes it before the game sees it.

    Windows doesn't care which side's Alt or Ctrl is held, so Right Alt+F4 still closes
    the game.
    """
    sides = {m.removeprefix("l").removeprefix("r") for m in key.modifiers}
    if any(m.endswith("win") for m in key.modifiers) or key.key.endswith("win"):
        return "the Windows key"
    for modifier in sides:
        if reason := _WINDOWS_SHORTCUTS.get((modifier, key.key)):
            return reason
    return None


def check(additions: Iterable[Addition], actions: Iterable[Action]) -> None:
    """Refuse additions that clash with existing bindings, each other, or bound actions."""
    actions = list(actions)
    used = used_keys(actions)
    inputs: dict[str, list[str]] = {}
    for action in actions:  # an action can be in several contexts: any binding counts
        inputs.setdefault(action.name, []).extend(map(str, action.inputs))
    seen: dict[str, str] = {}
    for addition in additions:
        key = KeyInput.parse(addition.key)
        if key is None:
            raise LayoutError(f"{addition.key!r} isn't a key")
        canonical = str(key)
        if reason := reserved_by_windows(key):
            raise LayoutError(f"{canonical} is a Windows shortcut ({reason})")
        if addition.action not in inputs:
            raise LayoutError(f"{addition.action} isn't an action in this game build")
        if bound := inputs[addition.action]:
            on = ", ".join(dict.fromkeys(bound))
            raise LayoutError(f"{addition.action} is already on {on}, which it would replace")
        if canonical in used:
            raise LayoutError(f"{canonical} is already used by {', '.join(used[canonical])}")
        if canonical in seen:
            raise LayoutError(
                f"{canonical} is proposed for both {seen[canonical]} and {addition.action}"
            )
        seen[canonical] = addition.action


def build(
    profile: ET.Element,
    additions: Iterable[Addition],
    actions: Iterable[Action],
    name: str,
    description: str = "",
    *,
    restore: Iterable[str] = (),
) -> ET.Element:
    """A new profile: the player's current bindings (from actionmaps.xml) plus ``additions``.

    ``actions`` are the effective keybinds, used to check for clashes and to find
    every control context (actionmap) each action appears in. ``restore`` names
    actions whose keyboard and mouse rebinds are dropped, giving them back their
    game defaults; joystick bindings stay.
    """
    actions = list(actions)
    additions = list(additions)
    check(additions, actions)
    source = profile.find("ActionProfiles") if profile.tag == "ActionMaps" else profile
    if source is None:
        raise LayoutError("actionmaps.xml has no ActionProfiles element")

    root = ET.Element(
        "ActionMaps",
        version=source.get("version", "1"),
        optionsVersion=source.get("optionsVersion", "2"),
        rebindVersion=source.get("rebindVersion", "2"),
        profileName=name,
    )
    header = ET.SubElement(
        root, "CustomisationUIHeader", label=name, description=description, image=""
    )
    devices = ET.SubElement(header, "devices")
    ET.SubElement(devices, "keyboard", instance="1")
    ET.SubElement(devices, "mouse", instance="1")
    for instance in sorted(_joysticks_used(source)):
        ET.SubElement(devices, "joystick", instance=str(instance))

    for child in source:
        root.append(copy.deepcopy(child))
    _restore_defaults(root, set(restore))

    maps = {m.get("name", ""): m for m in root.iter("actionmap")}
    contexts: dict[str, list[str]] = {}
    for action in actions:
        contexts.setdefault(action.name, []).append(action.actionmap)
    for addition in additions:
        for map_name in dict.fromkeys(contexts[addition.action]):
            actionmap = maps.get(map_name)
            if actionmap is None:
                actionmap = ET.SubElement(root, "actionmap", name=map_name)
                maps[map_name] = actionmap
            action_element = next(
                (a for a in actionmap.iter("action") if a.get("name") == addition.action), None
            )
            if action_element is None:
                action_element = ET.SubElement(actionmap, "action", name=addition.action)
            ET.SubElement(action_element, "rebind", input=f"kb1_{addition.key}")
    ET.indent(root, space=" ")
    return root


def write(root: ET.Element, folder: Path) -> Path:
    """Save a profile where the game's "load control settings" menu looks. Never overwrites."""
    name = root.get("profileName", "Tycho")
    path = folder / f"layout_{name}_exported.xml"
    if path.exists():
        raise FileExistsError(f"{path} already exists; choose another name")
    folder.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=False) + b"\n")
    return path


def _restore_defaults(root: ET.Element, names: set[str]) -> None:
    for actionmap in root.iter("actionmap"):
        for action in list(actionmap.iter("action")):
            if action.get("name") not in names:
                continue
            for rebind in list(action.iter("rebind")):
                if rebind.get("input", "")[:2] in ("kb", "mo"):
                    action.remove(rebind)
            if not len(action):
                actionmap.remove(action)


def _joysticks_used(source: ET.Element) -> set[int]:
    instances: set[int] = set()
    for rebind in source.iter("rebind"):
        if match := _JOYSTICK.match(rebind.get("input", "")):
            instances.add(int(match.group(1)))
    return instances
