"""The player's keybinds: the game's defaults, with their own changes on top.

The defaults live in Data.p4k (Data\\Libs\\Config\\defaultProfile.xml, as CryXmlB).
The player's changes live in actionmaps.xml, which lists only what they rebound,
per device: "kb1_" keyboard, "mo1_" mouse, "js1_"... joysticks, "gp1_" gamepad.

Tycho presses keyboard keys and mouse buttons, so only those inputs are kept.
Rebinding an action on a joystick leaves its keyboard key in place.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace

from tycho.game.localization import resolve

DEFAULTS_P4K_PATH = r"Data\Libs\Config\defaultProfile.xml"
MODIFIERS = frozenset({"lalt", "ralt", "lctrl", "rctrl", "lshift", "rshift"})
_PRESSABLE_DEVICES = {"kb": "keyboard", "mo": "mouse"}
_MOUSE_PREFIXES = ("mouse", "mwheel", "maxis")
_DEVICE_PREFIX = re.compile(r"^(kb|mo)\d+_")


@dataclass(frozen=True, slots=True)
class KeyInput:
    """One key or mouse button, with any modifiers held: "ralt+r", "mouse1"."""

    key: str
    modifiers: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> KeyInput | None:
        """Parse "ralt+r", "f6+lalt" or "kb1_ralt+i". Returns None for an empty binding."""
        text = _DEVICE_PREFIX.sub("", text.strip()).strip()  # "kb1_ " is a cleared binding
        if not text:
            return None
        parts = [p.strip() for p in text.split("+") if p.strip()]
        keys = [p for p in parts if p not in MODIFIERS]
        if len(keys) > 1:
            return None  # chords of two ordinary keys aren't something Tycho will press
        if not keys:  # only modifiers, e.g. "lctrl"; sorted so "lalt+lctrl" == "lctrl+lalt"
            ordered = sorted(parts)
            return cls(ordered[-1], tuple(ordered[:-1]))
        return cls(keys[0], tuple(sorted(p for p in parts if p in MODIFIERS)))

    @property
    def device(self) -> str:
        return "mouse" if self.key.startswith(_MOUSE_PREFIXES) else "keyboard"

    @property
    def pressable(self) -> bool:
        """Axes (mouse movement, wheels as axes) can't be pressed like a button."""
        return not self.key.startswith("maxis")

    def __str__(self) -> str:
        return "+".join((*self.modifiers, self.key))


@dataclass(frozen=True, slots=True)
class Action:
    name: str
    actionmap: str
    label: str
    """Readable name from the game's text, e.g. "Landing System (Toggle)". May be ""."""
    inputs: tuple[KeyInput, ...]
    """Keyboard keys and mouse buttons that trigger it. Empty if unbound."""
    activation: str = ""
    """How the game reads the key: "press", "tap", "hold", "double_tap"... "" for the default."""
    customized: bool = False
    """The player changed this binding from the game's default."""
    multi_tap: int = 0
    """Presses needed, from a multiTap attribute; 0 when the binding doesn't say."""

    @property
    def bound(self) -> bool:
        return any(i.pressable for i in self.inputs)

    @property
    def taps(self) -> int:
        """How many times to press the key to trigger the action."""
        if self.multi_tap > 0:
            return self.multi_tap
        return 2 if self.activation.startswith("double_tap") else 1


@dataclass(frozen=True, slots=True)
class ActivationMode:
    """How the game interprets presses for an action, from defaultProfile's ActivationModes.

    Thresholds are seconds; -1 means "not used".
    """

    name: str
    on_press: bool = True
    on_hold: bool = False
    on_release: bool = False
    multi_tap: int = 1
    press_threshold: float = -1.0
    """Hold at least this long before it counts (delayed_press)."""
    release_threshold: float = -1.0
    """Release within this long for it to count (tap)."""


class Keybinds:
    """Every action and its effective key, looked up by name, plus the game's activation modes."""

    def __init__(
        self, actions: Iterable[Action], modes: Mapping[str, ActivationMode] | None = None
    ) -> None:
        self.modes: dict[str, ActivationMode] = dict(modes or {})
        self._by_name: dict[str, list[Action]] = {}
        for action in actions:
            self._by_name.setdefault(action.name, []).append(action)

    def get(self, name: str) -> Action | None:
        """An action by name. Some appear in several groups; a bound copy wins."""
        copies = self._by_name.get(name, [])
        return next((a for a in copies if a.bound), copies[0] if copies else None)

    def __iter__(self) -> Iterator[Action]:
        return (a for copies in self._by_name.values() for a in copies)

    def __len__(self) -> int:
        return sum(len(copies) for copies in self._by_name.values())


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
    rebinds: dict[tuple[str, str], dict[str, list[KeyInput]]] = {}
    activations: dict[tuple[str, str], str] = {}
    multi_taps: dict[tuple[str, str], int] = {}
    for actionmap in profile.iter("actionmap"):
        for action in actionmap.iter("action"):
            key = (actionmap.get("name", ""), action.get("name", ""))
            for rebind in action.iter("rebind"):
                text = rebind.get("input", "")
                device = _PRESSABLE_DEVICES.get(text[:2])
                if device is None:
                    continue  # joystick or gamepad
                per_device = rebinds.setdefault(key, {}).setdefault(device, [])
                parsed = KeyInput.parse(text)
                if parsed is not None:
                    per_device.append(parsed)
                if mode := rebind.get("activationMode"):
                    activations[key] = mode
                if taps := _int(rebind.get("multiTap")):
                    multi_taps[key] = taps

    result: list[Action] = []
    for action in defaults:
        changes = rebinds.get((action.actionmap, action.name))
        if not changes:
            result.append(action)
            continue
        kept = [i for i in action.inputs if i.device not in changes]
        inputs = tuple(kept + [i for device in changes.values() for i in device])
        key = (action.actionmap, action.name)
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
