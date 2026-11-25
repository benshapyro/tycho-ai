"""Asking for a route by voice (#588): the words that ask, the places they name, the
pack's ``[route]`` table, and the session handing it to the flow (``test_route`` has
the flow itself, on its fake star map)."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, cast

import pytest

from tests.fakes.fake_keys import FakeClock, FakeFocus, FakeKeySender
from tests.fakes.fake_uex import FakeTradeUex
from tests.test_route import KEYBINDS, Game, _key_went_down  # pyright: ignore[reportPrivateUsage]
from tests.test_runtime import rig, run
from tycho.games import BUILT_IN
from tycho.games.star_citizen.places import UexPlaces, best_place
from tycho.input import Presser
from tycho.packs import STAR_CITIZEN, as_run, built_in
from tycho.packs.routes import PlaceError, Route, read
from tycho.runtime.conversation import would_speak
from tycho.runtime.messages import Progress, Result
from tycho.runtime.plotting import ROUTE, Plotter, asked_place

SC = built_in(STAR_CITIZEN)
assert SC.route is not None
ROUTE_TABLE: Route = replace(
    SC.route, flow=replace(SC.route.flow, beat_seconds=0.0, read_every=0.0)
)
LINES = ROUTE_TABLE.flow.lines


class FakePlaces:
    def __init__(self, *names: str, error: Exception | None = None) -> None:
        self.names, self.error = names, error
        self.asked: list[str] = []

    async def find(self, said: str) -> str | None:
        self.asked.append(said)
        if self.error is not None:
            raise self.error
        return next((name for name in self.names if name.lower() == said), None)

    def name_of(self, route_id: str) -> str | None:
        return None


def _plotter(game: Game, places: FakePlaces | None = None, **kw: Any) -> Plotter:
    sender = FakeKeySender()
    sender.on_event = lambda: _key_went_down(sender, game)
    clock = FakeClock()
    presser = Presser(sender, FakeFocus(True), sleep=clock.sleep, clock=clock, game="SC")
    options: dict[str, Any] = {
        "route": ROUTE_TABLE,
        "places": places or FakePlaces("Levski"),
        "reader": game,
        "clicker": game,
        "log": game,
        "keybinds": lambda: KEYBINDS,
        "lists": SC.safety,
        "presser": presser,
    } | kw
    return Plotter(**options)


def _answer(plotter: Plotter, said: str, trying: bool = False) -> tuple[str, str, Progress]:
    progress = Progress()

    async def scenario() -> tuple[str, str]:
        with ThreadPoolExecutor(1) as keys:
            return await plotter.answer(said, keys, threading.Event(), trying, progress)

    status, detail = asyncio.run(scenario())
    return status, detail, progress


# --- The words that ask ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "place"),
    [
        ("plot a route to Levski", "levski"),
        ("Plot a course to Area 18.", "area 18"),
        ("Tycho, set a course for Port Tressler please", "port tressler"),
        ("can you plot a route to New Babbage for me", "new babbage"),
        ("navigate to Lorville", "lorville"),
        ("set route to the Pyro Gateway", "the pyro gateway"),
    ],
)
def test_a_route_phrase_then_a_place_asks_for_a_route(said: str, place: str) -> None:
    assert asked_place(said, ROUTE_TABLE.say) == place


@pytest.mark.parametrize(
    "said",
    [
        "how do I plot a route to Levski",  # a question for the brain
        "plot a route to",  # no place
        "plot a route to please",
        "landing gear",
        "what's the best route to sell Laranite",
    ],
)
def test_other_words_never_ask_for_a_route(said: str) -> None:
    assert asked_place(said, ROUTE_TABLE.say) is None


# --- Answering -----------------------------------------------------------------------------


def test_the_place_is_found_and_the_route_plotted() -> None:
    game = Game()
    status, _detail, progress = _answer(_plotter(game), "levski")
    assert (status, progress.say) == ("done", "Route set to Levski.")
    assert progress.commands == (ROUTE,)
    assert "click on the search box" in progress.pressed
    assert game.route_id == "levski_all-001"


def test_a_place_tycho_doesnt_know_is_said_and_nothing_is_pressed() -> None:
    game = Game()
    status, _detail, progress = _answer(_plotter(game), "lefsky")
    assert status == "not_understood"
    assert progress.say == "I don't know a place called lefsky."
    assert progress.pressed == []
    assert not game.nav


def test_without_the_list_of_places_nothing_is_pressed() -> None:
    game = Game()
    places = FakePlaces(error=PlaceError("offline"))
    status, detail, progress = _answer(_plotter(game, places), "levski")
    assert (status, progress.say) == ("failed", LINES.no_places)
    assert "offline" in detail
    assert not game.nav


def test_try_it_never_plots() -> None:
    game = Game()
    places = FakePlaces("Levski")
    status, _detail, progress = _answer(_plotter(game, places), "levski", trying=True)
    assert (status, progress.say) == ("refused", LINES.trying)
    assert places.asked == []
    assert not game.nav


def test_with_key_actions_off_nothing_is_pressed_and_tycho_says_why() -> None:
    game = Game()
    plotter = _plotter(game, lists=as_run(SC, key_actions=False).safety)
    status, _detail, progress = _answer(plotter, "levski")
    assert status == "refused"
    assert progress.say
    assert progress.pressed == []
    assert not game.nav


def test_tycho_says_how_a_route_ended() -> None:
    said = Result(1, "failed", commands=(ROUTE,), say=LINES.stuck)
    assert would_speak(said)
    assert not would_speak(Result(1, "done", commands=(ROUTE,)))


def test_the_session_hands_a_route_request_to_the_plotter() -> None:
    game = Game()

    async def scenario() -> Result | None:
        r = rig("plot a route to Levski", plotter=_plotter(game))
        r.say()
        result = await r.session.idle()
        await r.session.close()
        return result

    result = run(scenario)
    assert result is not None
    assert (result.status, result.commands, result.say) == (
        "done",
        (ROUTE,),
        LINES.done.format(name="Levski"),
    )
    assert game.route_id == "levski_all-001"


# --- The pack's [route] table --------------------------------------------------------------


def _table() -> dict[str, object]:
    assert SC.route is not None
    flow = SC.route.flow
    lines = {name: getattr(flow.lines, name) for name in flow.lines.__dataclass_fields__}
    return {
        "say": list(SC.route.say),
        "places": SC.route.places,
        **{key: getattr(flow, key) for key in ("nav", "open_map", "enter", "clear", "plot")},
        "close_map": flow.close_map,
        **{key: getattr(flow, key) for key in ("search_box", "results", "no_results")},
        **{key: getattr(flow, key) for key in ("route_set", "route_line", "seated")},
        "close_marks": list(flow.close_marks),
        "close_offset": list(flow.close_offset),
        "close_reach": flow.close_reach,
        "lines": lines,
    }


def _read(table: object, built_in: bool = True, sees: bool = True) -> Route | None:
    return read(table, "pack.toml", built_in, sees, BUILT_IN.places)


def test_star_citizen_plots_routes_with_its_own_table() -> None:
    assert SC.route is not None
    assert "plot a route to" in SC.route.say
    assert SC.route.places in BUILT_IN.places
    assert _read(_table()) == SC.route
    assert _read(None) is None


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"places": "nowhere"}, "no built-in places part"),
        ({"nav": "Not An Action"}, "must name a keybind action"),
        ({"close_offset": [0.3]}, "close_offset"),
        ({"close_reach": 0}, "close_reach"),
        ({"say": []}, "say"),
        ({"surprise": 1}, "unknown keys"),
    ],
)
def test_a_bad_route_table_is_refused(change: dict[str, object], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        _read(_table() | change)


def test_route_lines_are_checked() -> None:
    table = _table()
    lines = cast(dict[str, str], table["lines"])
    missing = {name: line for name, line in lines.items() if name != "done"}
    with pytest.raises(ValueError, match="missing lines: done"):
        _read(table | {"lines": missing})
    with pytest.raises(ValueError, match="only"):
        _read(table | {"lines": lines | {"done": "Set to {place}."}})


def test_only_a_built_in_pack_with_a_window_and_a_log_may_plot() -> None:
    with pytest.raises(ValueError, match="built into Tycho"):
        _read(_table(), built_in=False)
    with pytest.raises(ValueError, match="window and a game_state"):
        _read(_table(), sees=False)


# --- Places, from UEX's list ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "name"),
    [
        ("area 18", "Area 18"),
        ("a18", "Area 18"),
        ("lorvile", "Lorville"),
        ("wide forest", "ARC-L1 Wide Forest Station"),
        ("the pyro gateway", "Pyro Gateway"),
        ("arccorp", "ArcCorp"),
        ("terminus", "Terminus"),
        ("nowhere at all", None),
    ],
)
def test_uex_places_find_the_name_the_map_knows(said: str, name: str | None) -> None:
    places = UexPlaces(FakeTradeUex())
    assert asyncio.run(places.find(said)) == name


def test_uex_places_offline_is_a_place_error() -> None:
    source = FakeTradeUex()
    source.offline = True
    with pytest.raises(PlaceError, match="offline"):
        asyncio.run(UexPlaces(source).find("levski"))


def test_uex_places_name_a_route_id_by_its_first_word() -> None:
    places = UexPlaces(FakeTradeUex())
    asyncio.run(places.find("lorville"))
    assert places.name_of("lorville_city-001") == "Lorville"
    assert places.name_of("area18_all-002") == "Area 18"
    assert places.name_of("rs_ext_pyro3_leo") is None


def test_a_place_whose_name_starts_with_a_filler_word_still_matches() -> None:
    rows = [{"outpost_name": "The Pit"}, {"city_name": "Area 18"}]
    assert best_place(rows, "the pit") == "The Pit"
    assert best_place(rows, "the area 18") == "Area 18"


def test_with_key_actions_off_no_place_is_looked_up() -> None:
    places = FakePlaces("Levski")
    lists = as_run(SC, key_actions=False).safety
    status, _detail, progress = _answer(_plotter(Game(), places, lists=lists), "levski")
    assert (status, progress.say) == ("refused", lists.keys_off)
    assert places.asked == []


def test_a_slow_list_of_places_says_why_in_the_log() -> None:
    class Slow(FakePlaces):
        async def find(self, said: str) -> str | None:
            raise TimeoutError

    status, detail, _progress = _answer(_plotter(Game(), Slow()), "levski")
    assert (status, detail) == ("failed", "no list of places: it took too long")
