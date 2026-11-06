"""UEX's trade data from memory (satisfies ``knowledge.WikiSource``): a handful of
commodities, terminals, prices and routes shaped like the real API's."""

from collections.abc import Mapping
from typing import Any

from tycho.knowledge import WikiError

NOW = 1_793_650_000
"""When the fake's prices were "reported" (a Unix time on 2 November 2026)."""


def commodity(ident: int, name: str, code: str) -> dict[str, Any]:
    return {"id": ident, "name": name, "code": code, "is_available_live": 1}


def terminal(ident: int, displayname: str, **place: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": ident,
        "displayname": displayname,
        "type": "commodity",
        "star_system_name": "Stanton",
        "id_star_system": 68,
    }
    record.update(place)
    return record


COMMODITIES = [
    commodity(47, "Laranite", "LARA"),
    commodity(48, "Laranite (Raw)", "LARA"),
    commodity(30, "Caranite", "CARA"),
    commodity(58, "Quantainium", "QUAN"),
    commodity(60, "Medical Supplies", "MEDS"),
    commodity(20, "Gold", "GOLD"),
    commodity(21, "Gold (Ore)", "GOLD"),
]
AREA18 = terminal(12, "Area18", city_name="Area 18", id_city=1, planet_name="ArcCorp", id_planet=4)
ARC_L1 = terminal(
    1,
    "ARC-L1 Wide Forest Station",
    space_station_name="ARC-L1 Wide Forest Station",
    id_space_station=1,
    planet_name="ArcCorp",
    id_planet=4,
    orbit_name="ArcCorp Lagrange Point 1",
    id_orbit=326,
)
LORVILLE = terminal(
    20, "Lorville", city_name="Lorville", id_city=2, planet_name="Hurston", id_planet=1
)
HUR_L1 = terminal(
    21,
    "HUR-L1 Green Glade Station",
    space_station_name="HUR-L1 Green Glade Station",
    id_space_station=5,
    planet_name="Hurston",
    id_planet=1,
    orbit_name="Hurston Lagrange Point 1",
    id_orbit=330,
)
NEW_BABBAGE = terminal(
    30, "New Babbage", city_name="New Babbage", id_city=3, planet_name="MicroTech", id_planet=6
)
REFINERY = terminal(
    40,
    "MIC-L5 Modern Icarus Station",
    space_station_name="MIC-L5 Modern Icarus Station",
    id_space_station=9,
    planet_name="MicroTech",
    id_planet=6,
    type="commodity_raw",
)
GATEWAY = terminal(
    50,
    "Pyro Gateway",
    space_station_name="Pyro Gateway",
    id_space_station=12,
)
RUIN = terminal(
    60,
    "Ruin Station",
    space_station_name="Ruin Station",
    id_space_station=20,
    star_system_name="Pyro",
    id_star_system=64,
    moon_name="Terminus",
    id_moon=70,
)
TERMINALS = [AREA18, ARC_L1, LORVILLE, HUR_L1, NEW_BABBAGE, REFINERY, GATEWAY, RUIN]


def price(
    where: dict[str, Any],
    *,
    sell: int = 0,
    buy: int = 0,
    scu_sell: int = 0,
    scu_buy: int = 0,
    age_hours: float = 1.0,
) -> dict[str, Any]:
    return {
        "id_terminal": where["id"],
        "price_sell": sell,
        "price_buy": buy,
        "scu_sell": scu_sell,
        "scu_buy": scu_buy,
        "date_modified": NOW - int(age_hours * 3600),
    }


PRICES: dict[int, list[dict[str, Any]]] = {
    47: [
        price(AREA18, sell=8500, scu_sell=1784),
        price(NEW_BABBAGE, sell=9100, age_hours=20),
        price(LORVILLE, buy=7541, scu_buy=608),
        price(GATEWAY, sell=9000),
    ],
    58: [price(LORVILLE, sell=170000, scu_sell=21)],
    60: [price(RUIN, sell=5500), price(GATEWAY, sell=5200)],
}
RAW_PRICES: dict[int, list[dict[str, Any]]] = {48: [price(REFINERY, sell=1281)]}


def route(
    start: dict[str, Any],
    end: dict[str, Any],
    name: str,
    ident: int,
    *,
    buy: int,
    sell: int,
    scu: int,
) -> dict[str, Any]:
    return {
        "id_commodity": ident,
        "commodity_name": name,
        "id_terminal_origin": start["id"],
        "id_terminal_destination": end["id"],
        "id_planet_origin": start.get("id_planet", 0),
        "price_origin": buy,
        "price_destination": sell,
        "scu_reachable": scu,
        "distance": 40,
        "date_added": NOW - 1800,
    }


ROUTES = [
    route(LORVILLE, NEW_BABBAGE, "Laranite", 47, buy=7541, sell=9100, scu=608),
    route(LORVILLE, AREA18, "Laranite", 47, buy=7541, sell=8500, scu=608),
    route(HUR_L1, NEW_BABBAGE, "Medical Supplies", 60, buy=4000, sell=4300, scu=5000),
    route(AREA18, NEW_BABBAGE, "Processed Food", 70, buy=1202, sell=1500, scu=12000),
    route(ARC_L1, LORVILLE, "Quantainium", 58, buy=100000, sell=103000, scu=10),
]


class FakeTradeUex:
    def __init__(self) -> None:
        self.offline = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        query = dict(params or {})
        self.calls.append((path, query))
        if self.offline:
            raise WikiError(f"{path}: offline")
        match path:
            case "/commodities":
                return {"status": "ok", "data": COMMODITIES}
            case "/terminals":
                return {"status": "ok", "data": TERMINALS}
            case "/commodities_prices":
                return {"status": "ok", "data": PRICES.get(int(query["id_commodity"]), [])}
            case "/commodities_raw_prices":
                return {"status": "ok", "data": RAW_PRICES.get(int(query["id_commodity"]), [])}
            case "/commodities_routes":
                return {"status": "ok", "data": self._routes(query)}
            case _:
                raise WikiError(f"{path}: not found")

    def _routes(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        if "id_terminal_origin" in query:
            return [r for r in ROUTES if r["id_terminal_origin"] == query["id_terminal_origin"]]
        if "id_planet_origin" in query:
            return [r for r in ROUTES if r["id_planet_origin"] == query["id_planet_origin"]]
        raise WikiError("missing_one_required_inputs")
