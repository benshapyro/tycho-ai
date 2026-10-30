"""Star Citizen key names, mapped to what Windows sends.

Keys are sent as hardware scan codes (scan code set 1), which games read more
reliably than virtual-key codes. Keys on the extended part of the keyboard
(right Ctrl and Alt, arrows, the navigation block, numpad Enter and /) carry
the extended flag.
"""

from dataclasses import dataclass
from typing import Literal

type Device = Literal["key", "mouse", "wheel"]


@dataclass(frozen=True, slots=True)
class Code:
    """One thing Windows can press: a keyboard key, a mouse button, or a wheel notch."""

    device: Device
    value: int
    """Scan code for keys; button number (1-5) for mouse; notches (+1 up, -1 down) for the wheel."""
    extended: bool = False

    @property
    def holdable(self) -> bool:
        """A wheel notch happens once; keys and buttons can be held."""
        return self.device != "wheel"


def _scan(value: int, extended: bool = False) -> Code:
    return Code("key", value, extended)


_LETTERS = (
    dict(
        zip(
            "qwertyuiop",
            range(0x10, 0x1A),
            strict=True,
        )
    )
    | dict(zip("asdfghjkl", range(0x1E, 0x27), strict=True))
    | dict(zip("zxcvbnm", range(0x2C, 0x33), strict=True))
)

_NAMED: dict[str, Code] = {
    **{letter: _scan(code) for letter, code in _LETTERS.items()},
    **{str(n): _scan(0x02 + n - 1) for n in range(1, 10)},
    "0": _scan(0x0B),
    **{f"f{n}": _scan(0x3B + n - 1) for n in range(1, 11)},
    "f11": _scan(0x57),
    "f12": _scan(0x58),
    "escape": _scan(0x01),
    "minus": _scan(0x0C),
    "equals": _scan(0x0D),
    "backspace": _scan(0x0E),
    "tab": _scan(0x0F),
    "lbracket": _scan(0x1A),
    "rbracket": _scan(0x1B),
    "enter": _scan(0x1C),
    "semicolon": _scan(0x27),
    "apostrophe": _scan(0x28),
    "grave": _scan(0x29),
    "backslash": _scan(0x2B),
    "comma": _scan(0x33),
    "period": _scan(0x34),
    "slash": _scan(0x35),
    "space": _scan(0x39),
    "capslock": _scan(0x3A),
    "numlock": _scan(0x45),
    "scrolllock": _scan(0x46),
    "lctrl": _scan(0x1D),
    "rctrl": _scan(0x1D, extended=True),
    "lshift": _scan(0x2A),
    "rshift": _scan(0x36),
    "lalt": _scan(0x38),
    "ralt": _scan(0x38, extended=True),
    "np_0": _scan(0x52),
    "np_1": _scan(0x4F),
    "np_2": _scan(0x50),
    "np_3": _scan(0x51),
    "np_4": _scan(0x4B),
    "np_5": _scan(0x4C),
    "np_6": _scan(0x4D),
    "np_7": _scan(0x47),
    "np_8": _scan(0x48),
    "np_9": _scan(0x49),
    "np_period": _scan(0x53),
    "np_add": _scan(0x4E),
    "np_subtract": _scan(0x4A),
    "np_multiply": _scan(0x37),
    "np_divide": _scan(0x35, extended=True),
    "np_enter": _scan(0x1C, extended=True),
    "insert": _scan(0x52, extended=True),
    "delete": _scan(0x53, extended=True),
    "home": _scan(0x47, extended=True),
    "end": _scan(0x4F, extended=True),
    "pgup": _scan(0x49, extended=True),
    "pgdn": _scan(0x51, extended=True),
    "up": _scan(0x48, extended=True),
    "down": _scan(0x50, extended=True),
    "left": _scan(0x4B, extended=True),
    "right": _scan(0x4D, extended=True),
    **{f"mouse{n}": Code("mouse", n) for n in range(1, 6)},
    "mwheel_up": Code("wheel", 1),
    "mwheel_down": Code("wheel", -1),
}
# Some bindings name symbol keys by the character instead.
KEYS: dict[str, Code] = _NAMED | {
    symbol: _NAMED[name]
    for symbol, name in {
        "[": "lbracket",
        "]": "rbracket",
        ";": "semicolon",
        "'": "apostrophe",
        ",": "comma",
        ".": "period",
        "/": "slash",
        "\\": "backslash",
        "-": "minus",
        "=": "equals",
        "`": "grave",
    }.items()
}


class UnsupportedKey(ValueError):
    """A key name Tycho doesn't know how to press."""


def code_for(name: str) -> Code:
    """The Windows code for a Star Citizen key name, e.g. "ralt" or "np_add"."""
    try:
        return KEYS[name.lower()]
    except KeyError:
        raise UnsupportedKey(f"Tycho can't press {name!r} yet") from None
