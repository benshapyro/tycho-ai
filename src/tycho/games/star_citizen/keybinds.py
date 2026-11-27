"""The player's keybinds: the game's defaults, with their own changes on top.

The defaults live in Data.p4k (Data\\Libs\\Config\\defaultProfile.xml, as CryXmlB).
The player's changes live in actionmaps.xml, which lists only what they rebound,
per device: "kb1_" keyboard, "mo1_" mouse, "js1_"... joysticks, "gp1_" gamepad.

Tycho presses keyboard keys and mouse buttons, so only those are an action's
``inputs``. Keyboard and mouse share one slot per action: a keyboard rebind replaces
the default mouse button too, and the other way round (seen in game: binding Right
Alt+Q to quantum engage took it off left click). Rebinding an action on a
joystick leaves its keyboard key in place.

Joystick inputs are kept apart, as ``stick_inputs``, and never pressed (#622): to warn
when push-to-talk is on a stick button bound in game, and to say an action is on the
player's stick rather than unbound. Each stick has its own slot: a "js2_" rebind
replaces only stick 2's. The default profile's joystick inputs are taken as stick 1's.
actionmaps.xml's ``<options type="joystick" instance="N" Product="...">`` names each.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import replace

from tycho.games.star_citizen.localization import resolve
from tycho.keybinds import Action, ActivationMode, KeyInput, StickInput

DEFAULTS_P4K_PATH = r"Data\Libs\Config\defaultProfile.xml"
_PRESSABLE_DEVICES = {"kb": "keyboard", "mo": "mouse"}
_STICK = re.compile(r"js(\d+)_(.*)", re.DOTALL)
_DEFAULT_STICK = 1


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
            sticks = (
                _stick_input(f"js{_DEFAULT_STICK}_{text}")
                for text in _default_inputs(element, "joystick")
            )
            actions.append(
                Action(
                    name=element.get("name", ""),
                    actionmap=actionmap.get("name", ""),
                    label=resolve(element.get("UILabel", ""), strings),
                    inputs=tuple(inputs),
                    activation=activation,
                    multi_tap=multi_tap,
                    stick_inputs=tuple(s for s in sticks if s is not None),
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
    stick_rebinds: dict[tuple[str, str], dict[int, list[StickInput]]] = {}
    for actionmap in profile.iter("actionmap"):
        for action in actionmap.iter("action"):
            key = (actionmap.get("name", ""), action.get("name", ""))
            for rebind in action.iter("rebind"):
                text = rebind.get("input", "")
                if match := _STICK.fullmatch(text):  # never pressed: kept apart (#622)
                    slot = stick_rebinds.setdefault(key, {}).setdefault(int(match[1]), [])
                    if (stick := _stick_input(text)) is not None:  # "js1_ " clears it
                        slot.append(stick)
                    continue
                if text[:2] not in _PRESSABLE_DEVICES:
                    continue  # gamepad
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
        if key in stick_rebinds:
            changed = stick_rebinds[key]
            kept = tuple(s for s in action.stick_inputs if s.stick not in changed)
            new = tuple(s for inputs in changed.values() for s in inputs)
            action = replace(action, stick_inputs=kept + new)
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


def parse_sticks(profile: ET.Element) -> dict[int, str]:
    """The sticks actionmaps.xml numbers, by number: the game's name for each, with the
    device's ID in braces ("VKBsim Gladiator EVO R {0200231D-0000-...}")."""
    sticks: dict[int, str] = {}
    for options in profile.iter("options"):
        if options.get("type") != "joystick":
            continue
        if (number := _int(options.get("instance"))) > 0:
            sticks[number] = (options.get("Product") or "").strip()
    return sticks


def _stick_input(text: str) -> StickInput | None:
    """ "js2_button12" as stick 2's button12; None for a cleared one ("js1_ ")."""
    match = _STICK.fullmatch(text.strip())
    if match is None or not match[2].strip():
        return None
    return StickInput(int(match[1]), match[2].strip().lower())


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
