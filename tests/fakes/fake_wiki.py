"""A Star Citizen Wiki API that answers from memory (satisfies ``knowledge.WikiSource``)."""

from collections.abc import Mapping
from typing import Any

from tycho.knowledge import WikiError


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
        self.calls: list[str] = []

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        self.calls.append(path)
        if self.offline:
            raise WikiError(f"{path}: offline")
        if path == "/api/v2/game-versions":
            return {"data": [{"code": "4.9.0-LIVE.0"}, {"code": self.version, "is_default": True}]}
        if path == "/api/v2/vehicles":
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
