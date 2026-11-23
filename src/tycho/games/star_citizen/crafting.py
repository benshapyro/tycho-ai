"""A crafting recipe as the brain is told it (ADR 15, #597): the ingredients, the time,
how the blueprint is unlocked, and what each material's quality improves."""

from collections.abc import Mapping
from typing import Any, cast


def recipe(blueprint: Mapping[str, Any]) -> dict[str, object]:
    needs: list[str] = []
    for part in cast(list[Any], blueprint.get("ingredients") or []):
        name, scu, count = dig(part, "name"), dig(part, "quantity_scu"), dig(part, "quantity")
        amount = f"{scu:g} SCU" if isinstance(scu, int | float) else f"x{count}" if count else ""
        needs.append(f"{name} {amount}".strip())
    out: dict[str, object] = {
        "ingredients": needs,
        "craft_time": blueprint.get("craft_time_label"),
        "known_from_start": bool(blueprint.get("is_available_by_default")),
        "unlocked_by_missions": blueprint.get("unlocking_missions_count"),
    }
    improves, least = _quality(blueprint)
    out["quality_improves"] = improves
    out["min_quality"] = least
    if needs:
        out["materials_note"] = MATERIALS_NOTE
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


MATERIALS_NOTE = (
    "if the player asked how to make it or where to get these, they want where each "
    "material comes from too: before answering, call mining_lookup for each material "
    "(and commodity_prices to buy one), all at once"
)
LOWEST_QUALITY = 1
"""A recipe's minimum quality (0 to 1000) at or below this is any quality: not said."""


def _quality(blueprint: Mapping[str, Any]) -> tuple[dict[str, list[str]], dict[str, int]]:
    """What each material's quality improves in what's made ("Iron": ["Impact
    Force", "Fire Rate"]), and any minimum quality above the lowest."""
    improves: dict[str, list[str]] = {}
    least: dict[str, int] = {}
    for group in cast(list[Any], blueprint.get("requirement_groups") or []):
        labels = [
            str(dig(m, "label"))
            for m in cast(list[Any], dig(group, "modifiers") or [])
            if dig(m, "label")
        ]
        for child in cast(list[Any], dig(group, "children") or []):
            name = dig(child, "name")
            if not name:
                continue
            if labels:
                have = improves.setdefault(str(name), [])
                have.extend(label for label in labels if label not in have)
            quality = dig(child, "min_quality")
            if isinstance(quality, int | float) and quality > LOWEST_QUALITY:
                least[str(name)] = int(quality)
    return improves, least


def dig(record: Any, *path: str) -> Any:
    value: Any = record
    for step in path:
        if not isinstance(value, Mapping):
            return None
        value = cast(Mapping[str, Any], value).get(step)
    return value
