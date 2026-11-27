"""The general keybind types: a key, an action and its keys, how the game reads a press,
and every action looked up by name (ADR 19).

Every game that presses keys uses these. Reading a game's own files into them is that
game's code: Star Citizen's readers are in ``tycho.games.star_citizen.keybinds``.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

MODIFIERS = frozenset({"lalt", "ralt", "lctrl", "rctrl", "lshift", "rshift"})
_MOUSE_PREFIXES = ("mouse", "mwheel", "maxis")
_DEVICE_PREFIX = re.compile(r"^(kb|mo)\d+_")
_BUTTON = re.compile(r"button(\d+)")


@dataclass(frozen=True, slots=True)
class KeyInput:
    """One key or mouse button, with any modifiers held: "ralt+r", "mouse1"."""

    key: str
    modifiers: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> KeyInput | None:
        """Parse "ralt+r", "f6+lalt" or "kb1_ralt+i". Returns None for an empty binding."""
        # Key names are case-insensitive: the game itself writes both "ralt+k" and "ralt+K".
        text = _DEVICE_PREFIX.sub("", text.strip()).strip().lower()  # "kb1_ " is cleared
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
class StickInput:
    """One input on a joystick, as the game numbers them (#622): the stick (1, 2...) and
    the input on it ("button12", "hat1_up", "x"). Tycho never presses one: it's kept to
    warn when push-to-talk is on it too, and to say an action is on the player's stick."""

    stick: int
    input: str

    @property
    def button(self) -> int | None:
        """The button's number, counted from 1 as the game does; None for anything else
        (an axis, a hat, a button held with another)."""
        match = _BUTTON.fullmatch(self.input)
        return int(match.group(1)) if match else None


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
    stick_inputs: tuple[StickInput, ...] = ()
    """Its joystick inputs (#622). Never pressed: ``inputs`` are all Tycho presses."""

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
    """Every action and its effective key, looked up by name, plus the game's activation
    modes and the sticks the game numbers."""

    def __init__(
        self,
        actions: Iterable[Action],
        modes: Mapping[str, ActivationMode] | None = None,
        sticks: Mapping[int, str] | None = None,
    ) -> None:
        self.modes: dict[str, ActivationMode] = dict(modes or {})
        self.sticks: dict[int, str] = dict(sticks or {})
        """The game's name for each stick it numbers (``StickInput.stick``), as its own
        file writes it: "VKBsim Gladiator EVO R {0200231D-0000-0000-0000-504944564944}"."""
        self._by_name: dict[str, list[Action]] = {}
        for action in actions:
            self._by_name.setdefault(action.name, []).append(action)

    def get(self, name: str) -> Action | None:
        """An action by name. Some appear in several groups; a bound copy wins."""
        copies = self._by_name.get(name, [])
        return next((a for a in copies if a.bound), copies[0] if copies else None)

    def on_stick(self, name: str) -> Action | None:
        """The action, if it's bound on a stick and has no key Tycho can press in any
        group (#622): so Tycho says it's on the stick rather than unbound."""
        copies = self._by_name.get(name, [])
        if any(a.bound for a in copies):
            return None
        return next((a for a in copies if a.stick_inputs), None)

    def __iter__(self) -> Iterator[Action]:
        return (a for copies in self._by_name.values() for a in copies)

    def __len__(self) -> int:
        return sum(len(copies) for copies in self._by_name.values())


def key_text(key: KeyInput) -> str:
    """A key as the player knows it: "Right Alt + O", "Numpad 8", "Mouse 4"."""
    parts = [_MODIFIERS.get(m, m.capitalize()) for m in key.modifiers]
    parts.append(_key_name(key.key))
    return " + ".join(parts)


_MODIFIERS = {
    "lalt": "Left Alt",
    "ralt": "Right Alt",
    "lctrl": "Left Ctrl",
    "rctrl": "Right Ctrl",
    "lshift": "Left Shift",
    "rshift": "Right Shift",
}


def _key_name(key: str) -> str:
    if key in _MODIFIERS:
        return _MODIFIERS[key]
    if key.startswith("np_"):
        return "Numpad " + key[3:].replace("_", " ").title()
    if key.startswith("mouse") and key[5:].isdigit():
        return f"Mouse {key[5:]}"
    if len(key) == 1 or (key[0] == "f" and key[1:].isdigit()):
        return key.upper()  # "O", "5", "F5"
    return _NAMES.get(key, key.replace("_", " ").title())


_NAMES = {"esc": "Esc", "pgup": "Page Up", "pgdn": "Page Down", "ins": "Insert", "del": "Delete"}


def readable(action: str) -> str:
    """An action with no label in the game's files, as words (#465):
    "v_toggle_landing_system" is "Toggle landing system", not the raw id."""
    words = action.replace("-", "_").split("_")
    if len(words) > 1 and len(words[0]) == 1:  # a one-letter group, such as "v_"
        words = words[1:]
    said = " ".join(word for word in words if word)
    return said[:1].upper() + said[1:] if said else action
