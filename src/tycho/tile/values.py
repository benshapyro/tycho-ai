"""What two Tile settings may hold, checked as the settings file is read (ADR 16). Out of
``settings.py`` (ADR 19's line limit)."""

from typing import Annotated

from pydantic import AfterValidator

from tycho.tile.cards import ALL_KINDS


def _card_kinds(value: str) -> str:
    kinds = [k.strip().lower() for k in value.replace(";", ",").split(",") if k.strip()]
    if unknown := [k for k in kinds if k not in ALL_KINDS]:
        raise ValueError(f"no card is called {', '.join(unknown)}")
    return ", ".join(dict.fromkeys(k for k in kinds if k != "confirm"))


def _screen(value: str) -> str:
    """``game``, or a screen as Windows names its device (``\\\\.\\DISPLAY2``, #447)."""
    value = value.strip()
    if not value or value.lower() == "game":
        return "game"
    if not value.startswith("\\\\.\\") or len(value) > 32:
        raise ValueError("use game, or a screen as Windows names it, such as \\\\.\\DISPLAY2")
    return value


CardKinds = Annotated[str, AfterValidator(_card_kinds)]
"""Kinds of card, by comma; never the question, which always shows."""
ScreenName = Annotated[str, AfterValidator(_screen)]
