"""Tycho's design system, as data: one source for the Tile and the setup app (#2, #12).

Cockpit-native, never official: a dark glass core that never changes, and one accent
that follows the ship the player is flying. Warnings keep their own colours, so a red
question still reads as red in a red cockpit. No fankit fonts or logos: the fonts are
free ones, and the Tile shows no manufacturer marks.

The manufacturer accents are estimates (docs/research/manufacturer-ui.md), to confirm
in game. Keyed by the prefix the game's own files and Game.log give a ship
(``DRAK_Cutlass_Black``), so no lookup table of ship names is needed.
"""

from dataclasses import dataclass
from typing import Final

TYCHO_ACCENT: Final = "#9fd8ff"
"""Tycho's own accent: when no ship is known, or the player picks it."""


@dataclass(frozen=True, slots=True)
class Core:
    """The colours that never change with the ship."""

    void: str = "#070a10"
    glass: str = "#080e18"
    """The card's background, drawn at the see-through level the player chooses."""
    text: str = "#e4ebf5"
    dim: str = "#8c99ae"
    rule: str = "#233046"
    good: str = "#6fd39b"
    caution: str = "#f2b84b"
    danger: str = "#ff6a5f"


CORE: Final = Core()

MAKER_ACCENTS: Final[dict[str, str]] = {
    "AEGS": "#cfe6ff",  # Aegis Dynamics: cool white on dark
    "ANVL": "#7fd1b9",  # Anvil Aerospace
    "ARGO": "#4fb3ff",  # Argo: the shared default style
    "XIAN": "#b6ed68",  # Aopoa
    "BANU": "#e8c27a",
    "CNOU": "#4fb3ff",  # Consolidated Outland: default style
    "CRUS": "#8cc8ff",  # Crusader Industries
    "DRAK": "#f5901e",  # Drake Interplanetary: orange
    "ESPR": "#4fb3ff",  # Esperia: default style
    "GAMA": "#6de6f2",  # Gatac
    "GRIN": "#f4d35e",  # Greycat Industrial
    "KRIG": "#4fb3ff",  # Kruger: default style
    "MISC": "#58ddd1",
    "MRAI": "#ff647c",  # Mirai
    "ORIG": "#f2f4f7",  # Origin Jumpworks: pale
    "RSI": "#4fb3ff",  # Roberts Space Industries: blue
    "TMBL": "#ddb582",  # Tumbril
}
"""Estimated HUD colour by the manufacturer prefix of a ship's game name."""


def accent_for(ship: str | None) -> str:
    """The accent for a ship's game name (``DRAK_Cutlass_Black``), or Tycho's own."""
    if not ship:
        return TYCHO_ACCENT
    prefix = ship.split("_", 1)[0].upper()
    return MAKER_ACCENTS.get(prefix, TYCHO_ACCENT)


@dataclass(frozen=True, slots=True)
class Fonts:
    """Free fonts only (SIL Open Font License), each with Windows' own as a fallback."""

    label: tuple[str, ...] = ("Saira Condensed", "Bahnschrift Condensed", "Arial Narrow")
    text: tuple[str, ...] = ("Saira Semi Condensed", "Bahnschrift", "Segoe UI")
    number: tuple[str, ...] = ("Share Tech Mono", "Cascadia Mono", "Consolas")


FONTS: Final = Fonts()

TEXT_SIZES: Final[dict[str, float]] = {"small": 0.85, "medium": 1.0, "large": 1.2, "xl": 1.4}
"""How much bigger than medium each text size is. The Tile grows with its text."""

SEE_THROUGH: Final = (0, 60)
"""How see-through the card's background may be, in percent. Text never fades."""
