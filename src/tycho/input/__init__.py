"""Tycho's hands: planning and sending key presses through the player's own keybinds."""

from tycho.input.keys import Code, UnsupportedKey, code_for
from tycho.input.plan import NotPressable, Press, plan
from tycho.input.presser import FocusCheck, KeySender, Presser, Receipt

__all__ = [
    "Code",
    "FocusCheck",
    "KeySender",
    "NotPressable",
    "Press",
    "Presser",
    "Receipt",
    "UnsupportedKey",
    "code_for",
    "plan",
]
