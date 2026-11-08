"""How long each look-up takes against the live APIs (#150): free, no keys.

Runs a few questions from the play sessions through the real look-ups (UEX and the
Star Citizen Wiki API), each several times, and prints the median and slowest time
for each, with the look-up's time limit beside it. Each round starts with fresh
look-ups, so nothing is reused from the round before; the commodity and terminal
lists load before the clock starts, as Tycho loads them at startup.

    uv run python tools/bench/lookup_times.py            # 5 rounds
    uv run python tools/bench/lookup_times.py --rounds 10

Run it on main and on a branch to compare. It's about 40 requests a round, well
inside UEX's 120 a minute if rounds are a few seconds apart (they are).
"""

import argparse
import asyncio
import statistics
import time
from collections.abc import Mapping

from tycho.brain.lookups import Lookup, run_lookup
from tycho.knowledge import UexApi, WikiApi
from tycho.knowledge.item_lookup import ItemLookup
from tycho.knowledge.mining import MiningLookup
from tycho.knowledge.trade_lookup import trade_lookups

QUESTIONS: list[tuple[str, str, Mapping[str, object]]] = [
    # "I'm in Pyro at Obituary", after "best route for refined gold" (play session, #150)
    (
        "gold from Obituary",
        "trade_routes",
        {"start": "Obituary", "commodity": "gold", "end": "", "cargo_scu": 0},
    ),
    (
        "routes from Obituary",
        "trade_routes",
        {"start": "Obituary", "commodity": "", "end": "", "cargo_scu": 0},
    ),
    (
        "gold in Pyro",
        "trade_routes",
        {"start": "Pyro", "commodity": "gold", "end": "", "cargo_scu": 0},
    ),
    (
        "routes from Hurston",
        "trade_routes",
        {"start": "Hurston", "commodity": "", "end": "", "cargo_scu": 0},
    ),
    (
        "laranite from Area 18",
        "trade_routes",
        {"start": "Area 18", "commodity": "laranite", "end": "", "cargo_scu": 96},
    ),
    (
        "where to sell lindinium",
        "commodity_prices",
        {"commodity": "lindinium", "place": ""},
    ),
    ("refining quantainium", "refining", {"ore": "quantainium", "place": ""}),
    ("where to buy a Cutlass", "ship_purchases", {"ship": "Cutlass Black"}),
    ("an item", "item_lookup", {"names": ["P4-AR"]}),
    ("where to mine hadanite", "mining_lookup", {"resource": "hadanite", "place": ""}),
]


def fresh_lookups() -> dict[str, Lookup]:
    lookups: list[Lookup] = [
        ItemLookup(WikiApi()),
        MiningLookup(WikiApi()),
        *trade_lookups(UexApi()),
    ]
    return {lookup.name: lookup for lookup in lookups}


async def one_round() -> dict[str, tuple[float, str | None]]:
    lookups = fresh_lookups()
    await asyncio.gather(*(lookup.prepare() for lookup in lookups.values()))
    times: dict[str, tuple[float, str | None]] = {}
    for label, name, arguments in QUESTIONS:
        lookup = lookups[name]
        start = time.perf_counter()
        # A long limit, so the real time is measured, not the cut-off.
        result = await run_lookup(lookup, dict(arguments), seconds=30.0)
        error = result.content if result.is_error else None
        times[label] = (time.perf_counter() - start, error)
    return times


def main() -> None:
    parser = argparse.ArgumentParser(description="Time each look-up against the live APIs.")
    parser.add_argument("--rounds", type=int, default=5)
    rounds = parser.parse_args().rounds
    limits = {
        name: float(getattr(lookup, "seconds", 0.0)) for name, lookup in fresh_lookups().items()
    }
    results: dict[str, list[tuple[float, str | None]]] = {label: [] for label, _, _ in QUESTIONS}
    for n in range(rounds):
        for label, timing in asyncio.run(one_round()).items():
            results[label].append(timing)
        print(f"round {n + 1} of {rounds} done")
    print(f"\n{'question':<26}{'look-up':<18}{'median':>8}{'slowest':>9}{'limit':>7}  errors")
    for label, name, _ in QUESTIONS:
        seconds = [s for s, _ in results[label]]
        errors = [error for _, error in results[label] if error]
        print(
            f"{label:<26}{name:<18}{statistics.median(seconds):>7.2f}s{max(seconds):>8.2f}s"
            f"{limits[name]:>6.1f}s  {len(errors)}"
        )
        if errors:
            print(f"    first error: {errors[0][:120]}")


if __name__ == "__main__":
    main()
