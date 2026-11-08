"""The Star Citizen Wiki API's items and blueprints from memory (satisfies
``knowledge.WikiSource``). Like the real one, a name filter matches as a substring,
ignoring case but not punctuation or spaces."""

import asyncio
from collections.abc import Mapping
from typing import Any

from tests.fakes.fake_wiki import FakeSearch
from tycho.knowledge import WikiError


def item(name: str, kind: str = "WeaponPersonal", **fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "uuid": name.lower().replace(" ", "-"),
        "name": name,
        "type": kind,
        "classification_label": kind,
        "manufacturer": {"name": "Klaus & Werner"},
        "size": 1,
    }
    record.update(fields)
    return record


def shop(where: str, auec: int) -> dict[str, Any]:
    return {"terminal_name": where, "price_buy": auec}


ITEMS = [
    item(
        "FS-9 LMG",
        classification_label="Medium Weapon",
        manufacturer={"name": "Behring Applied Technology"},
        personal_weapon={
            "class": "Ballistic",
            "type": "LMG",
            "damage_per_shot": 14.5,
            "rpm": 650,
            "effective_range": 1200,
            "magazine_size": 75,
            "modes": [{"damage_per_second": 157.1}],
        },
        uex_prices={"purchase": [shop("Guns Orbituary", 7100), shop("Guns Checkmate", 7029)]},
        is_craftable=True,
    ),
    item("FS-9 Magazine (75 cap)", "WeaponAttachment"),
    item("Arrowhead Sniper Rifle", personal_weapon={"type": "Sniper Rifle"}),
    item("Arrow Headhunters Reaper Livery", "Paints"),
    item(
        "Atlas",
        "QuantumDrive",
        classification_label="Quantum Drive",
        grade="A",
        quantum_drive={
            "fuel_consumption_scu_per_gm": 0.005,
            "standard_jump": {"drive_speed_formatted": "259.1 Mm/s", "spool_up_time": 4.2},
            "travel_time_10gm": {"formatted": "0:55"},
        },
    ),
    item("Atlasium", "Cargo"),
    item("Glacier", "Cooler", cooler={"coolant_segment_generation": 38}),
    item('Cambio-II "Glacier" SRT', personal_weapon={"type": "Utility"}),
    item(
        "Bulwark",
        "Shield",
        grade="C",
        shield={"max_health": 2160, "regen_rate": 410},
        uex_prices={
            "purchase": [
                shop("Platinum CRU-L1", 55129),
                shop("Platinum CRU-L4", 49500),
                shop("Dumper's GrimHEX", 49500),
                shop("Somewhere Dear", 90000),
            ]
        },
    ),
    item(
        "CF-117 Bulldog Repeater",
        "WeaponGun",
        vehicle_weapon={
            "type": "Laser Repeater",
            "damage_per_shot": 17.5,
            "rpm": 750,
            "range": 1605,
            "capacity": 0,
            "modes": [{"damage_per_second": 218.7}],
        },
    ),
    item(
        "Morozov-SH Helmet",
        "Char_Armor_Helmet",
        suit_armor={
            "armor_type": "Helmet",
            "damage_resistance": {
                "impact": 0.65,
                "physical": {"multiplier": 0.6},
                "energy": {"multiplier": 0.6},
                "stun": {"multiplier": 0.4},
            },
        },
    ),
    item("Morozov-SH Arms", "Char_Armor_Arms"),
    item("P4-AR Rifle", personal_weapon={"type": "Assault Rifle"}),
]
BLUEPRINTS = [
    {
        "output_name": "FS-9 LMG",
        "craft_time_label": "4 minutes",
        "is_available_by_default": False,
        "unlocking_missions_count": 21,
        "ingredients": [
            {"name": "Lindinium", "quantity_scu": 0.06},
            {"name": "Iron", "quantity_scu": 0.03},
            {"name": "Dolivine", "quantity": 1},
        ],
    }
]


class FakeItemsWiki:
    """``slow`` holds queries (name filters) that take ``delay`` seconds, as a query
    the real API hasn't seen before can."""

    def __init__(
        self,
        *,
        slow: frozenset[str] = frozenset(),
        delay: float = 1.0,
        extra: list[dict[str, Any]] | None = None,
    ) -> None:
        self.extra = extra or []
        self.slow = slow
        self.delay = delay
        self.offline = False
        self.search = FakeSearch()
        self.calls: list[dict[str, Any]] = []

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        query = dict(params or {})
        self.calls.append({"path": path, **query})
        if self.offline:
            raise WikiError(f"{path}: offline")
        if path == "/api/search":
            return await self.search.answer(params)
        if path == "/api/items":
            name = str(query.get("filter[name]", ""))
            if name in self.slow:
                await asyncio.sleep(self.delay)
            found = [i for i in [*ITEMS, *self.extra] if name.lower() in i["name"].lower()]
            return {"data": found[: int(query.get("limit", 30))], "meta": {"total": len(found)}}
        if path == "/api/blueprints":
            name = query.get("filter[output.name]")
            return {"data": [b for b in BLUEPRINTS if b["output_name"] == name]}
        raise WikiError(f"{path}: not found")
