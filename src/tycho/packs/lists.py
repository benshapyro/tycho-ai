"""A game pack's lists for the safety gate (``safety.toml``, ADR 18), as loaded. The
rules that use them stay in ``safety.py``, and the brain's in ``brain/unasked.py``."""

from collections.abc import Mapping
from dataclasses import dataclass, field

LOOSENING = ("not_danger", "mode_guards", "state_guards", "accepted_risks")
"""The safety lists that make a check let something through. Only a pack that ships
inside Tycho, reviewed like code, may set them (ADR 18)."""
LISTS = frozenset({"confirm", "travel", "facts", "toggles", "named", *LOOSENING})
"""Every list ``safety.toml`` may hold."""


@dataclass(frozen=True, slots=True)
class Safety:
    """A game's lists for the safety gate (``safety.toml``). The rules that use them, and
    the danger-word backstop on top of them, stay in ``safety.py``."""

    confirm: frozenset[str] = frozenset()
    """Actions that always ask first."""
    travel: frozenset[str] = frozenset()
    """Actions that ask when the brain chose them from looser words (ADR 14)."""
    facts: Mapping[str, str] = field(default_factory=dict[str, str])
    """Facts the game state can report, with what the gate says when one is missing."""
    not_danger: tuple[str, ...] = ()
    """Words in harmless action names the backstop would catch (built-in packs only)."""
    mode_guards: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    """Setup actions, and the control contexts they switch off (built-in packs only)."""
    state_guards: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    """Facts, and the control contexts they rule out (built-in packs only)."""
    accepted_risks: frozenset[str] = frozenset()
    """Contexts a command may accept the risk of (built-in packs only)."""
    named: frozenset[str] = frozenset()
    """Actions the brain presses only when the player's words single them out (#648):
    with a word Tycho can't place, or only words another action has too, it asks which
    they mean instead. Any pack may list them: they only make Tycho press less."""
    toggles: tuple[str, ...] = ()
    """Words in an action's name or label that mean it flips or cycles a state, so
    Tycho says it can't see which way it went (#29). Any pack may list them: they only
    make Tycho claim less."""
    keys_off: str = ""
    """Set when Tycho may press nothing (advisor mode, #191), to the sentence that says
    why. The gate's first check refuses every press while it's set. It comes from the
    pack's mode or the player's ``key_actions`` setting (``as_run``), never from
    ``safety.toml``."""

    def flips(self, name: str, label: str) -> bool:
        """Whether the action ``name`` (``label``) is one of the pack's toggles."""
        said = f"{name} {label}".lower()
        return any(word in said for word in self.toggles)
