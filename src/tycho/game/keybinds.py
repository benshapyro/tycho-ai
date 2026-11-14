"""The player's keybinds: the game's defaults, with their own changes on top.

The defaults live in Data.p4k (Data\\Libs\\Config\\defaultProfile.xml, as CryXmlB).
The player's changes live in actionmaps.xml, which lists only what they rebound,
per device: "kb1_" keyboard, "mo1_" mouse, "js1_"... joysticks, "gp1_" gamepad.

Tycho presses keyboard keys and mouse buttons, so only those inputs are kept.
Keyboard and mouse share one slot per action: a keyboard rebind replaces the
default mouse button too, and the other way round (seen in game: binding Right
Alt+Q to quantum engage took it off left click). Rebinding an action on a
joystick leaves its keyboard key in place.
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import replace

from tycho.game.localization import resolve
from tycho.keybinds import Action, ActivationMode, KeyInput

DEFAULTS_P4K_PATH = r"Data\Libs\Config\defaultProfile.xml"
_PRESSABLE_DEVICES = {"kb": "keyboard", "mo": "mouse"}


def parse_defaults(root: ET.Element, strings: Mapping[str, str]) -> list[Action]:
    """Actions from the game's default profile, with labels resolved through ``strings``."""
    actions: list[Action] = []
    for actionmap in root.iter("actionmap"):
        for element in actionmap.iter("action"):
            inputs: list[KeyInput] = []
            activation = element.get("activationMode") or element.get("ActivationMode") or ""
            multi_tap = _int(element.get("multiTap"))
            for device in ("keyboard", "mouse"):
                for text in _default_inputs(element, device):
                    parsed = KeyInput.parse(text)
                    if parsed is not None:
                        inputs.append(parsed)
                child = element.find(device)
                if child is not None:
                    activation = activation or child.get("activationMode", "")
                    multi_tap = multi_tap or _int(child.get("multiTap"))
            actions.append(
                Action(
                    name=element.get("name", ""),
                    actionmap=actionmap.get("name", ""),
                    label=resolve(element.get("UILabel", ""), strings),
                    inputs=tuple(inputs),
                    activation=activation,
                    multi_tap=multi_tap,
                )
            )
    return actions


def parse_activation_modes(root: ET.Element) -> dict[str, ActivationMode]:
    """The game's activation modes (tap, hold, delayed_press...) with their timings."""
    modes: dict[str, ActivationMode] = {}
    for element in root.iter("ActivationMode"):
        name = element.get("name", "")
        if not name:
            continue
        modes[name] = ActivationMode(
            name=name,
            on_press=element.get("onPress", "1") == "1",
            on_hold=element.get("onHold", "0") == "1",
            on_release=element.get("onRelease", "0") == "1",
            multi_tap=_int(element.get("multiTap")) or 1,
            press_threshold=_float(element.get("pressTriggerThreshold")),
            release_threshold=_float(element.get("releaseTriggerThreshold")),
        )
    return modes


def apply_profile(defaults: Iterable[Action], profile: ET.Element) -> list[Action]:
    """Apply the player's actionmaps.xml on top of the defaults."""
    rebinds: dict[tuple[str, str], list[KeyInput]] = {}
    activations: dict[tuple[str, str], str] = {}
    multi_taps: dict[tuple[str, str], int] = {}
    for actionmap in profile.iter("actionmap"):
        for action in actionmap.iter("action"):
            key = (actionmap.get("name", ""), action.get("name", ""))
            for rebind in action.iter("rebind"):
                text = rebind.get("input", "")
                if text[:2] not in _PRESSABLE_DEVICES:
                    continue  # joystick or gamepad
                slot = rebinds.setdefault(key, [])  # a blank rebind ("kb1_ ") clears it
                if (parsed := KeyInput.parse(text)) is not None:
                    slot.append(parsed)
                if mode := rebind.get("activationMode"):
                    activations[key] = mode
                if taps := _int(rebind.get("multiTap")):
                    multi_taps[key] = taps

    result: list[Action] = []
    for action in defaults:
        key = (action.actionmap, action.name)
        if key not in rebinds:
            result.append(action)
            continue
        inputs = tuple(rebinds[key])  # replaces every default keyboard key and mouse button
        mode = activations.get(key, action.activation)
        taps = multi_taps.get(key, action.multi_tap)
        result.append(
            replace(action, inputs=inputs, activation=mode, multi_tap=taps, customized=True)
        )
    return result


def _int(text: str | None) -> int:
    try:
        return int(text) if text else 0
    except ValueError:
        return 0


def _float(text: str | None) -> float:
    try:
        return float(text) if text else -1.0
    except ValueError:
        return -1.0


def _default_inputs(action: ET.Element, device: str) -> list[str]:
    """A device's inputs, written as an attribute, a child, or a list of alternatives."""
    if (attribute := action.get(device)) is not None:
        return [attribute]
    child = action.find(device)
    if child is None:
        return []
    if (single := child.get("input")) is not None:
        return [single]
    return [data.get("input", "") for data in child.iter("inputdata")]
