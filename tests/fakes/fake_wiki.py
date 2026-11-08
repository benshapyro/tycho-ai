"""A Star Citizen Wiki API that answers from memory (satisfies ``knowledge.WikiSource``)."""

import asyncio
from collections.abc import Mapping
from typing import Any

from tycho.knowledge import WikiError

SEARCHED: dict[str, list[tuple[str, list[tuple[str, str, str | None]]]]] = {
    # The live API's unified search, GET /api/search?filter[query]=..., recorded on
    # 7 November 2026 (4.10.2-LIVE): each group's type, and each result's name, class
    # name and label (item_type_label for items, extra_label for vehicles). The answer
    # also has classification, classification_label, web_url and api_url, unread.
    "gallant": [
        ("items", [
            ("Gallant \"Red Alert\" Rifle", "klwe_rifle_energy_01_iae2201", "FPS Weapon"),
            ("Gallant \"Conviction Camo\" Rifle", "klwe_rifle_energy_01_sf01", "FPS Weapon"),
            ("Gallant \"Redline\" Rifle", "klwe_rifle_energy_01_store01", "FPS Weapon"),
            ("Gallant Rifle", "klwe_rifle_energy_01_test_inf_ammo", "FPS Weapon"),
            ("Gallant \"Boneyard\" Rifle", "klwe_rifle_energy_01_white02", "FPS Weapon"),
        ]),
        ("blueprints", [
            ("Gallant \"Midnight\" Rifle", "klwe_rifle_energy_01_black01", None),
            ("Gallant \"Nightstalker\" Rifle", "klwe_rifle_energy_01_black02", None),
            ("Gallant \"Executive Edition\" Rifle", "klwe_rifle_energy_01_gold01", None),
            ("Gallant \"Warhawk\" Rifle", "klwe_rifle_energy_01_green01", None),
            ("Gallant \"Desert Shadow\" Rifle", "klwe_rifle_energy_01_tan01", None),
        ]),
    ],
    "cutlass black": [
        ("vehicles", [
            ("Drake Cutlass Black", "DRAK_Cutlass_Black_BIS2950", "Multi-Role"),
            ("Cutlass Black PYAM Exec", "DRAK_Cutlass_Black_Exec_Military", "Multi-Role"),
            ("Cutlass Black PYAM Exec", "DRAK_Cutlass_Black_Exec_Stealth", "Multi-Role"),
            ("Drake Cutlass Black", "DRAK_Cutlass_Black", "Multi-Role"),
        ]),
        ("items", [
            ("Cutlass Black Ship Armor", "ARMR_DRAK_Cutlass_Black", "Armor"),
            ("Cutlass Black Ship Armor", "ARMR_DRAK_Cutlass_Blue", "Armor"),
            ("Cutlass Black Ship Armor", "ARMR_DRAK_Cutlass_Red", "Armor"),
            ("Cutlass Black Ship Armor", "ARMR_GLSN_Shiv", "Armor"),
            ("Cutlass Black Plushie", "Carryable_1H_CY_toy_plushy_cutlass_black_1_a", "Misc"),
        ]),
    ],
    "fs-9": [
        ("items", [
            ("FS-9 \"Evergreen\" LMG", "behr_lmg_ballistic_01_luminalia_green01", "FPS Weapon"),
            ("FS-9 LMG", "behr_lmg_ballistic_01", "FPS Weapon"),
            ("FS-9 Magazine (75 cap)", "behr_lmg_ballistic_01_mag", "Weapon Attachment"),
            ("FS-9 \"Blacklist\" LMG", "behr_lmg_ballistic_01_tint01", "FPS Weapon"),
            ("FS-9 \"Stoneface\" LMG", "behr_lmg_ballistic_01_xenothreat01", "FPS Weapon"),
        ]),
        ("blueprints", [
            ("FS-9 LMG", "behr_lmg_ballistic_01", None),
            ("FS-9 Magazine (75 cap)", "behr_lmg_ballistic_01_mag", None),
            ("FS-9 \"Blacklist\" LMG", "behr_lmg_ballistic_01_tint01", None),
            ("FS-9 \"Stoneface\" LMG", "behr_lmg_ballistic_01_xenothreat01", None),
            ("FS-9 \"Finish Line\" LMG", "behr_lmg_ballistic_01_yellow_grey01", None),
        ]),
    ],
}  # fmt: skip


def search_answer(query: str) -> dict[str, Any]:
    """The search's answer for ``query``, shaped as the API sends it: groups that
    found nothing are left out, and a query that found nothing is ``{"data": []}``."""
    groups = SEARCHED.get(query.lower().strip(), [])
    return {
        "data": [
            {
                "type": kind,
                "label": kind.title(),
                "results": [
                    {
                        "name": name,
                        "class_name": class_name,
                        "classification": None,
                        "classification_label": None,
                        "item_type_label": label if kind != "vehicles" else None,
                        "extra_label": label if kind == "vehicles" else None,
                        "web_url": "",
                        "api_url": "",
                    }
                    for name, class_name, label in results
                ],
            }
            for kind, results in groups
        ]
    }


class FakeSearch:
    """The unified search, from ``SEARCHED``: ``delay`` seconds before each answer,
    or a ``WikiError`` while ``down``."""

    def __init__(self, *, delay: float = 0.0, extra: dict[str, Any] | None = None) -> None:
        self.delay = delay
        self.down = False
        self.extra = extra or {}
        """Answers by query, sent as they are, for shapes the real API never sends."""
        self.asked: list[str] = []

    async def answer(self, params: Mapping[str, str | int] | None) -> Any:
        query = str((params or {}).get("filter[query]", ""))
        self.asked.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.down:
            raise WikiError("/api/search: HTTP 429")
        return self.extra.get(query) or search_answer(query)


def vehicle(name: str, maker: str = "Roberts Space Industries", **fields: Any) -> dict[str, Any]:
    """A vehicle record shaped like the API's, with only what Tycho reads."""
    record: dict[str, Any] = {
        "name": name,
        "game_name": f"{maker.split()[0]} {name}",
        "class_name": name.replace(" ", "_"),
        "manufacturer": {"name": maker},
        "is_spaceship": True,
        "career": "Multi-Role",
        "role": "Medium Freight",
        "size": {"en_EN": "Large", "de_DE": "Groß"},
        "crew": {"min": 1, "max": 4},
        "cargo_capacity": 96,
        "speed": {"scm": 190, "max": 1000},
        "msrp": 240,
    }
    record.update(fields)
    return record


class FakeWiki:
    def __init__(
        self, vehicles: list[dict[str, Any]], version: str = "4.10.2-LIVE.1", per_page: int = 2
    ) -> None:
        self.vehicles = vehicles
        self.version = version
        self.per_page = per_page
        self.offline = False
        self.search = FakeSearch()
        self.calls: list[str] = []

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        self.calls.append(path)
        if self.offline:
            raise WikiError(f"{path}: offline")
        if path == "/api/search":
            return await self.search.answer(params)
        if path == "/api/game-versions":
            return {"data": [{"code": "4.9.0-LIVE.0"}, {"code": self.version, "is_default": True}]}
        if path == "/api/vehicles":
            page = int((params or {}).get("page", 1))
            last = max(1, -(-len(self.vehicles) // self.per_page))
            start = (page - 1) * self.per_page
            return {
                "data": self.vehicles[start : start + self.per_page],
                "meta": {"last_page": last},
            }
        raise WikiError(f"{path}: not found")


def uex_vehicle(name: str, maker: str = "Drake Interplanetary", **fields: Any) -> dict[str, Any]:
    """A UEX vehicle record, shaped like its ``/vehicles`` list, marked as a concept."""
    record: dict[str, Any] = {
        "name": name,
        "name_full": f"{maker.split()[0]} {name}",
        "slug": name.lower().replace(" ", "-"),
        "company_name": maker,
        "scu": 0,
        "crew": "1",
        "length": 0,
        "width": 0,
        "height": 0,
        "pad_type": "",
        "is_concept": 1,
        "is_addon": 0,
        "is_spaceship": 1,
        "is_ground_vehicle": 0,
    }
    record.update(fields)
    return record


class FakeUex:
    """UEX's vehicle list from memory (satisfies ``knowledge.WikiSource``)."""

    def __init__(self, vehicles: list[dict[str, Any]]) -> None:
        self.vehicles = vehicles
        self.offline = False
        self.calls: list[str] = []

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        self.calls.append(path)
        if self.offline:
            raise WikiError(f"{path}: offline")
        if path == "/vehicles":
            return {"status": "ok", "data": self.vehicles}
        raise WikiError(f"{path}: not found")
