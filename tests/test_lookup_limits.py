"""The look-ups' time limits, fitted to timings on Ben's PC (#150)."""

from tycho.knowledge import item_lookup, items, mining, refining, trade_lookup
from tycho.runtime.session import LOOKING_SECONDS


def test_no_look_up_can_take_most_of_the_extra_time_it_gets() -> None:
    # A look-up moves the request's deadline LOOKING_SECONDS on (#166, #178). A limit
    # close to that left Claude no time to answer after a slow source gave up: each
    # leaves Claude at least 2 s. Every one measured under 1 s (8 November 2026).
    # Not here: ship_lookup (local data, LOOKUP_SECONDS), the fleet (a local file) and
    # web search (12 s, a separate Claude request that's slow by nature).
    limits = {
        "trade": trade_lookup.SECONDS,
        "items": item_lookup.SECONDS,
        "mining": mining.SECONDS,
        "refining": refining.SECONDS,
    }
    assert all(seconds <= LOOKING_SECONDS - 2.0 for seconds in limits.values()), limits


def test_an_item_and_its_recipe_both_fit_in_the_item_look_up() -> None:
    assert items.FIND_SECONDS + items.QUERY_SECONDS <= item_lookup.SECONDS


def test_a_stations_terminals_are_cut_off_before_the_trade_look_up_ends() -> None:
    assert trade_lookup.PART_SECONDS < trade_lookup.SECONDS
