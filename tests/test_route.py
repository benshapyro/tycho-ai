"""Plotting a route on the star map by voice (#588, ADR 3's amendments for it).

A small fake of the game: the map opens on its key, its search box takes letters only
after a click on it, Enter shows the results, the panel's close button gives the keys
back to the map, and only then does R plot (as the PC tests on 23 and 24 Nov 2026
found). The log writes what the real one does.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from tests.fakes.fake_keys import FakeClock, FakeFocus, FakeKeySender
from tycho.eyes import CaptureError
from tycho.eyes.words import Reading, Word
from tycho.games.star_citizen import gamelog, loglines
from tycho.input import Presser
from tycho.input.keys import Code, code_for
from tycho.keybinds import Action, Keybinds, KeyInput
from tycho.packs import STAR_CITIZEN, as_run, built_in
from tycho.packs.routes import RouteFlow, RouteLines
from tycho.runtime.route import Place, Plotted, id_names, plot
from tycho.situation import Logged

SC = built_in(STAR_CITIZEN)
WIDTH, HEIGHT = 2560, 1440
BOX = (230, 165, 120, 22)  # where "SEARCH LOCATIONS..." is read, as the PC test found
BOX_MIDDLE = (295, 176)  # the middle of both words, as the fake map draws them
OFFSET = (0.307, 0.0)
CLOSE = (round(BOX_MIDDLE[0] + OFFSET[0] * HEIGHT), BOX_MIDDLE[1])
LINES = RouteLines(
    done="Route set to {name}.",
    replacing="Replacing your route to {name}.",
    replacing_unnamed="Replacing your route.",
    on_foot="Sit down in your ship first.",
    no_map="I couldn't open the star map.",
    no_results="The map can't find {name}. It only searches the system you're in.",
    not_listed="The map didn't list {name}.",
    stuck="I couldn't finish on the map, so I left it open.",
    no_route="I couldn't plot a route to {name}. Your quantum fuel may be low.",
    other_place="The map plotted a route, but I can't tell it's to {name}. Check the map.",
    bad_name="I can't type {name} into the map.",
)
FLOW = RouteFlow(
    nav="v_master_mode_set_nav",
    open_map="v_starmap",
    enter="ui_textfield_enter",
    clear="mapui_action_clearroute",
    plot="mapui_action_planroute",
    close_map="v_starmap",
    search_box="search locations",
    results="results",
    no_results="no results",
    close_offset=OFFSET,
    close_marks=("x", "\u00d7"),
    close_reach=0.02,
    lines=LINES,
    route_set=gamelog.QUANTUM_ROUTE,
    route_line=loglines.ROUTE,
    seated=loglines.NO_ROUTE_ON_MAP,
    beat_seconds=0.0,
    read_every=0.0,
)


def _bound(name: str, key: str, actionmap: str) -> Action:
    found = KeyInput.parse(key)
    assert found is not None
    return Action(name=name, actionmap=actionmap, label="", inputs=(found,))


KEYBINDS = Keybinds(
    [
        _bound("v_master_mode_set_nav", "b", "spaceship_general"),
        _bound("v_starmap", "f2", "spaceship_general"),
        _bound("ui_textfield_enter", "enter", "ui_textfield"),
        _bound("mapui_action_clearroute", "c", "mapui"),
        _bound("mapui_action_planroute", "r", "mapui"),
    ]
)
KEYS = {code_for(key): name for key, name in (("b", "nav"), ("f2", "map"), ("enter", "enter"))}
LETTERS = {code_for(key): key for key in "abcdefghijklmnopqrstuvwxyz0123456789"} | {
    code_for("space"): " ",
    code_for("apostrophe"): "'",
}


@dataclass
class Game:
    """The star map as the PC tests found it, and its Game.log."""

    seated: bool = True
    route_id: str = ""
    """The route already set, if any."""
    places: dict[str, str] = field(default_factory=lambda: {"levski": "levski_all-001"})
    """What the map's search finds in this system: name to its id."""
    fuel: bool = True
    map_open: bool = False
    box_has_keys: bool = False
    typed: str = ""
    entered: bool = False
    nav: bool = False
    clicks: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    lines: list[Logged] = field(default_factory=list[Logged])
    log_quiet: bool = False
    """The map opens but no "No Route loaded!" line comes (a patch changed it)."""
    shows_x: bool = True
    takes_keys: bool = True
    """False: the clicked box ignores the keys Tycho sends, and they reach the game (#654)."""
    recent: tuple[str, ...] = ()
    """Places in the map's RECENT list, shown under the box before anything is typed."""
    stray: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        if self.route_id:
            self._log(loglines.ROUTE, self.route_id)

    # --- keys ---
    def key(self, code: Code) -> None:
        if self.box_has_keys and code in LETTERS:  # the box takes every letter, R and C too
            if self.takes_keys:
                self.typed += LETTERS[code]
            else:
                self.stray.append(LETTERS[code])
            return
        if code == code_for("enter") and self.box_has_keys:
            self.entered = True
            return
        what = KEYS.get(code)
        if what == "nav":
            self.nav = True
        elif what == "map":
            self.map_open = not self.map_open
            self.box_has_keys, self.typed, self.entered = False, "", False
            if self.map_open and self.seated and not self.route_id and not self.log_quiet:
                self._log(loglines.NO_ROUTE_ON_MAP)
        elif code == code_for("c") and self.map_open:
            self.route_id = ""
        elif code == code_for("r") and self.map_open and self.entered:
            if self.route_id or not self.fuel:
                return  # the game won't plot over a route, nor without fuel
            self.route_id = self.places.get(self.typed, "")
            if self.route_id:
                self._log(loglines.ROUTE, self.route_id)

    def _log(self, kind: str, name: str = "") -> None:
        self.lines.append(Logged(kind, "", None, len(self.lines) + 1, name))

    # --- the mouse ---
    def click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str:
        assert window == 7
        assert frame == (WIDTH, HEIGHT)
        assert self.nav, "NAV is set before any click"
        self.clicks.append((x, y))
        if not self.map_open:
            return ""  # in the cockpit: with NAV set, nothing fires
        if abs(x - BOX_MIDDLE[0]) < 20 and abs(y - BOX_MIDDLE[1]) < 20:
            self.box_has_keys = True
        elif abs(x - CLOSE[0]) < 20 and abs(y - CLOSE[1]) < 20:
            self.box_has_keys = False
        return ""

    # --- what the screen shows ---
    def read(self) -> Reading:
        if not self.map_open:
            return Reading(WIDTH, HEIGHT, (Word("FUEL", 100, 900, 60, 20, 0),), window=7)
        box = self.typed.upper().split() or ["SEARCH", "LOCATIONS..."]
        words = [Word(text, BOX[0] + 70 * i, BOX[1], 60, BOX[3], 0) for i, text in enumerate(box)]
        if not self.typed:
            for i, place in enumerate(("RECENT", *(p.upper() for p in self.recent))):
                words.append(Word(place, BOX[0], 220 + 40 * i, 90, 20, 10 + i))
        if (self.box_has_keys or self.entered) and self.shows_x:
            words.append(Word("x", CLOSE[0] - 6, CLOSE[1] - 8, 12, 16, 0))
        if self.entered:
            found = self.typed in self.places
            words.append(Word("RESULTS" if found else "NO", BOX[0], 220, 90, 20, 1))
            words.append(Word("(1)" if found else "RESULTS", BOX[0] + 100, 220, 30, 20, 1))
            if found:
                for i, text in enumerate(self.typed.upper().split()):
                    words.append(Word(text, BOX[0] + 80 * i, 280, 70, 20, 2))
        return Reading(WIDTH, HEIGHT, tuple(words), window=7)

    # --- the log ---
    def facts(self) -> frozenset[str]:
        return frozenset({gamelog.QUANTUM_ROUTE}) if self.route_id else frozenset()

    def last(self, kind: str) -> Logged | None:
        return next((line for line in reversed(self.lines) if line.kind == kind), None)

    def newest(self) -> int:
        return len(self.lines)


def _plot(
    game: Game,
    name: str = "Levski",
    *,
    focus: FakeFocus | None = None,
    name_of: Callable[[str], str | None] = lambda _id: None,
    **kw: object,
) -> Plotted:
    sender = FakeKeySender()
    sender.on_event = lambda: _key_went_down(sender, game)
    clock = FakeClock()
    presser = Presser(
        sender, focus or FakeFocus(True), sleep=clock.sleep, clock=clock, game="Star Citizen"
    )
    options: dict[str, object] = {
        "keybinds": KEYBINDS,
        "lists": SC.safety,
        "presser": presser,
        "reader": game,
        "clicker": game,
        "log": game,
        "cancel": threading.Event(),
        "clock": _Clock(),
        "name_of": name_of,
    } | kw
    return plot(Place(name), FLOW, **options)  # type: ignore[arg-type]


def _key_went_down(sender: FakeKeySender, game: Game) -> None:
    kind, code = sender.events[-1]
    if kind == "down" and isinstance(code, Code):
        game.key(code)


class _Clock:
    """Each look at the time moves it on a little, so a wait ends."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.1
        return self.now


def test_a_route_is_plotted_and_checked_by_the_logs_id() -> None:
    game = Game()
    done = _plot(game)
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.route_id == "levski_all-001"
    assert done.clicks == 2
    assert len(game.clicks) == 2
    assert game.clicks == [BOX_MIDDLE, CLOSE]
    assert not game.map_open  # closed again once it's done


def test_a_route_with_no_known_name_is_still_said_to_be_replaced() -> None:
    done = _plot(Game(route_id="rs_ext_pyro3_leo"))
    assert done.say == "Replacing your route. Route set to Levski."


def test_a_route_already_set_is_cleared_first_and_said() -> None:
    game = Game(route_id="rs_ext_pyro3_leo")
    done = _plot(game, name_of=lambda found: "Leo" if found == "rs_ext_pyro3_leo" else None)
    assert done.status == "done"
    assert done.say == "Replacing your route to Leo. Route set to Levski."
    assert game.route_id == "levski_all-001"


def test_on_foot_nothing_is_clicked_and_the_map_is_closed() -> None:
    game = Game(seated=False)
    done = _plot(game)
    assert (done.status, done.say) == ("refused", "Sit down in your ship first.")
    assert game.clicks == []
    assert game.typed == ""
    assert not game.map_open


def test_no_map_on_screen_means_nothing_is_clicked_or_typed() -> None:
    game = Game()

    class Blind:
        def read(self) -> Reading:
            raise CaptureError("no StarCitizen.exe window is open")

    done = _plot(game, reader=Blind())
    assert (done.status, done.say) == ("failed", "I couldn't open the star map.")
    assert game.clicks == []
    assert not done.typed


def test_a_place_not_in_this_system_is_said_and_r_is_never_pressed() -> None:
    game = Game()
    done = _plot(game, "Port Tressler")
    assert done.status == "failed"
    assert done.say == "The map can't find Port Tressler. It only searches the system you're in."
    assert game.clicks == [BOX_MIDDLE]  # no second click
    assert game.map_open  # left open for the player
    assert "R" not in done.pressed


def test_without_fuel_the_missing_route_line_is_said() -> None:
    game = Game(fuel=False)
    done = _plot(game)
    assert (done.status, done.say) == (
        "failed",
        "I couldn't plot a route to Levski. Your quantum fuel may be low.",
    )
    assert game.map_open


def test_a_route_to_another_id_is_never_said_as_done() -> None:
    game = Game(places={"levski": "rs_ext_pyro3_leo"})
    done = _plot(game)
    assert done.status == "failed"
    assert "can't tell it's to Levski" in done.say
    assert game.map_open


def test_no_close_button_where_the_panel_has_one_means_no_second_click() -> None:
    game = Game(shows_x=False)
    done = _plot(game)
    assert done.status == "failed"
    assert done.say == LINES.stuck
    assert game.clicks == [BOX_MIDDLE]
    assert game.route_id == ""


def test_a_map_with_no_seat_line_is_refused_after_its_wait() -> None:
    game = Game(log_quiet=True)
    done = _plot(game)
    assert done.status == "refused"
    assert game.clicks == []


def test_keys_off_presses_nothing() -> None:
    game = Game()
    done = _plot(game, lists=as_run(SC, key_actions=False).safety)
    assert done.status == "refused"
    assert done.say  # says why
    assert done.pressed == []
    assert not game.nav
    assert game.clicks == []


def test_a_missing_key_presses_nothing() -> None:
    game = Game()
    keybinds = Keybinds([a for a in KEYBINDS if a.name != "mapui_action_planroute"])
    done = _plot(game, keybinds=keybinds)
    assert done.status == "no_key"
    assert done.pressed == []
    assert not game.nav


def test_losing_focus_stops_the_typing() -> None:
    game = Game()
    # NAV, map, then the first two letters, then the game is no longer in front.
    done = _plot(game, focus=FakeFocus(True, True, True, True, False))
    assert done.status == "no_focus"
    assert game.typed == "le"
    assert game.route_id == ""


def test_a_cancel_stops_it_between_steps() -> None:
    game = Game()
    cancel = threading.Event()
    cancel.set()
    done = _plot(game, cancel=cancel)
    assert done.status == "cancelled"
    assert done.pressed == []
    assert game.clicks == []


@pytest.mark.parametrize("name", ["Levski\n", "", "x" * 41, "Levski; r", "Lorville/"])
def test_only_a_plain_name_is_ever_typed(name: str) -> None:
    game = Game()
    done = _plot(game, name)
    assert done.status == "refused"
    assert done.pressed == []
    assert game.clicks == []


@pytest.mark.parametrize(
    ("route_id", "name", "named"),
    [
        ("levski_all-001", "Levski", True),
        ("rs_ext_pyro3_leo", "Leo", True),
        ("rs_ext_pyro3_leo", "Levski", False),
        ("ObjectContainer_Lorville_City", "Lorville", True),
        ("stanton_station_l1", "Port Station", False),  # only common words
    ],
)
def test_the_id_names_the_place(route_id: str, name: str, named: bool) -> None:
    assert id_names(route_id, name) is named


# --- From the review of #636 -------------------------------------------------------------


def test_a_name_on_a_dangerous_key_is_never_typed() -> None:
    # If the box didn't take the keys after all, "l" would eject.
    game = Game()
    keybinds = Keybinds([*KEYBINDS, _bound("v_eject", "l", "spaceship_general")])
    done = _plot(game, keybinds=keybinds)
    assert done.status == "refused"
    assert done.pressed == []
    assert game.clicks == []


def test_a_map_that_closed_after_the_first_click_gets_no_letters() -> None:
    game = Game()

    class ClosesAfterClick:
        def click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str:
            game.click(window, frame, x, y)
            game.map_open = False  # the accepted gap: the map closed just then
            return ""

    done = _plot(game, clicker=ClosesAfterClick())
    assert done.status == "failed"
    assert game.typed == ""
    assert not done.typed


def test_windows_refusing_the_click_is_a_stop_not_a_crash() -> None:
    game = Game()

    class Refuses:
        def click(self, window: int, frame: tuple[int, int], x: int, y: int) -> str:
            raise OSError("SendInput failed")

    done = _plot(game, clicker=Refuses())
    assert (done.status, done.say) == ("failed", LINES.stuck)
    assert not done.typed


def test_a_map_marker_with_the_name_is_not_a_result() -> None:
    game = Game()
    real = game.read

    def read() -> Reading:
        reading = real()
        if not game.entered:
            return reading
        # The results list lost the place, but a marker far out on the map has its name.
        kept = tuple(w for w in reading.words if w.line != 2)
        marker = Word("LEVSKI", 1800, 900, 80, 20, 9)
        return Reading(reading.width, reading.height, (*kept, marker), reading.window)

    class Reader:
        def read(self) -> Reading:
            return read()

    done = _plot(game, reader=Reader())
    assert done.status == "failed"
    assert done.say == "The map didn't list Levski."
    assert game.clicks == [BOX_MIDDLE]


def test_a_box_that_takes_no_letters_gets_one_and_no_enter() -> None:
    game = Game(takes_keys=False)
    done = _plot(game)
    assert (done.status, done.say) == ("failed", LINES.not_typed)
    assert game.stray == ["l"]  # one letter, then it stopped
    assert "typed L" in done.pressed  # and the action log shows it
    assert not game.entered
    assert not game.route_id
    assert "ui_textfield_enter" not in done.pressed


def test_a_recent_list_is_not_the_search_results() -> None:
    game = Game(recent=("levski",))
    real = game.read

    def read() -> Reading:
        reading = real()
        if not game.entered:
            return reading
        # The search found nothing under the box; RESULTS shows only far out of the
        # panel, while the RECENT list under the box still names Levski.
        kept = tuple(w for w in reading.words if w.line not in (1, 2))
        recent = (Word("RECENT", BOX[0], 220, 90, 20, 10), Word("LEVSKI", BOX[0], 260, 90, 20, 11))
        elsewhere = Word("RESULTS", 1800, 600, 90, 20, 12)
        return Reading(reading.width, reading.height, (*kept, *recent, elsewhere), reading.window)

    class Reader:
        def read(self) -> Reading:
            return read()

    done = _plot(game, reader=Reader())
    assert done.status == "failed"
    assert game.clicks == [BOX_MIDDLE]  # never the close button
    assert not game.route_id


def test_a_caret_or_a_hyphenated_name_still_reads_as_typed() -> None:
    game = Game(places={"arc-l1": "arcl1_all-001"})
    real = game.read

    def read() -> Reading:
        reading = real()
        if game.typed and not game.entered:  # one OCR word, and the caret as an I
            kept = tuple(w for w in reading.words if w.line != 0)
            box = Word(game.typed.upper() + "I", BOX[0], BOX[1], 90, BOX[3], 0)
            return Reading(reading.width, reading.height, (*kept, box), reading.window)
        return reading

    class Reader:
        def read(self) -> Reading:
            return read()

    _plot(game, "ARC-L1", reader=Reader())
    assert game.entered


def test_the_name_must_show_in_the_box_before_enter() -> None:
    game = Game()
    real = game.read

    def read() -> Reading:
        reading = real()
        if game.typed and not game.entered:  # only part of the name reached the box
            kept = tuple(w for w in reading.words if w.line != 0)
            part = Word(game.typed[:2].upper(), BOX[0], BOX[1], 60, BOX[3], 0)
            return Reading(reading.width, reading.height, (*kept, part), reading.window)
        return reading

    class Reader:
        def read(self) -> Reading:
            return read()

    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("failed", LINES.not_typed)
    assert not game.entered


def test_typing_is_in_the_pressed_list() -> None:
    done = _plot(Game())
    assert done.status == "done"
    assert "typed Levski" in done.pressed


# --- The route record (TYCHO_ROUTE_RECORD, ADR 14's amendment of 25 Nov 2026) ---------------


class _Record:
    def __init__(self) -> None:
        self.told: list[tuple[str, Reading | None]] = []

    def __call__(self, why: str, read: Reading | None) -> None:
        self.told.append((why, read))


def test_a_failure_on_the_map_is_told_to_the_record_with_the_last_words() -> None:
    record = _Record()
    done = _plot(Game(), "Port Tressler", record=record)
    assert done.status == "failed"
    [(why, read)] = record.told
    assert why == done.detail
    assert read is not None
    assert read.words


def test_no_search_box_is_recorded_even_with_no_words() -> None:
    class Blind:
        def read(self) -> Reading:
            raise CaptureError("no StarCitizen.exe window is open")

    record = _Record()
    _plot(Game(), reader=Blind(), record=record)
    assert record.told == [("no frame showed the map's search box", None)]


def test_a_route_plotted_or_refused_before_the_map_is_never_recorded() -> None:
    record = _Record()
    assert _plot(Game(), record=record).status == "done"
    assert _plot(Game(), "Levski\nR", record=record).status == "refused"  # not a name
    assert _plot(Game(seated=False), record=record).status == "refused"  # on foot, map opened
    assert record.told == []


def test_a_record_that_breaks_still_ends_with_the_flows_own_line() -> None:
    def broken(_why: str, _read: Reading | None) -> None:
        raise RuntimeError("can't start new thread")

    done = _plot(Game(), "Port Tressler", record=broken)
    assert done.status == "failed"
    assert done.say


@dataclass
class _WarmGame(Game):
    """A reader that gets ready before the map opens, and is put away after (#661)."""

    warmed: list[bool] = field(default_factory=list[bool])
    """Whether the map was open each time it was warmed."""
    finished: int = 0

    def warm(self) -> None:
        self.warmed.append(self.map_open)

    def finish(self) -> None:
        self.finished += 1


def test_one_reading_process_is_warmed_before_the_map_and_put_away_after() -> None:
    game = _WarmGame()
    assert _plot(game).status == "done"
    assert (game.warmed, game.finished) == ([False], 1)


def test_the_reading_process_is_put_away_when_a_route_fails_too() -> None:
    game = _WarmGame()
    assert _plot(game, "Port Tressler").status == "failed"
    assert game.finished == 1
