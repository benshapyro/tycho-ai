"""Plotting a route on the star map by voice (#588, ADR 3's amendments for it).

A small fake of the game: the map opens on its key, its search box takes letters only
after a click on it, Enter shows the results, the panel's close button gives the keys
back to the map, and only then does R plot (as the PC tests on 23 and 24 Nov 2026
found). The log writes what the real one does.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace

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
from tycho.runtime.mappicks import under_header
from tycho.runtime.mapwords import alike, focused, heading, shows_alike
from tycho.runtime.route import Place, Plotted, id_names, plot
from tycho.situation import Logged

SC = built_in(STAR_CITIZEN)
WIDTH, HEIGHT = 2560, 1440
BOX = (230, 165, 120, 22)  # where "SEARCH LOCATIONS..." is read, as the PC test found
BOX_MIDDLE = (295, 176)  # the middle of both words, as the fake map draws them
OFFSET = (0.307, 0.0)
CLOSE = (round(BOX_MIDDLE[0] + OFFSET[0] * HEIGHT), BOX_MIDDLE[1])
TITLE = (BOX[0] + 45, 230)  # the middle of the place's name on its own panel
ROW_TOP, ROW = 260, 40  # the filtered list's rows while typing: where, and how far apart
PICKED = (BOX[0] + 65, 300)  # the place's name under RECENT, Enter having emptied the box
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
    settle_seconds=0.0,
    click_seconds=0.0,
    recent="recent",
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
    reads_letters: bool = True
    """False: text recognition reads nothing on the box's line, only the list under it."""
    close_at: tuple[int, int] = CLOSE
    """Where the box's x is drawn, and where a click gives the keys back to the map."""
    details: bool = False
    """Enter opens the place's own panel, its name just under the box, and no results list
    or x (Ben's PC try, 26 Nov 2026); a click on that name gives the keys back."""
    empties: bool = False
    """Enter picks the place and empties the box: its empty words back, the place under
    RECENT (read one letter wrong) and the whole list under ALL RESULTS, no x (Ben's
    Orison try, 26 Nov 2026); a click on the place's name gives the keys back."""
    keeps_hint: bool = False
    """The clicked box still shows its empty words until a letter lands, as on 24 Nov;
    False: it drops them for its own x once it has the keys (Ben's PC try, 26 Nov)."""
    slides: int = 0
    """Looks, after the map opens, in which its panel is still sliding in: the box is
    drawn further left each look before, and a click on it does nothing (Ben's PC try,
    25 Nov 2026)."""
    sliding: int = 0
    rows: tuple[str, ...] = ()
    """The filtered list's rows under ALL RESULTS while the name is typed, before Enter
    (sections too: "STANTON SYSTEM"); a click on the row that is the typed name picks it
    and gives the keys back (#682)."""
    calculates: int = 0
    """Looks, once the place is picked on the panel, in which the map still works out the
    fuel ("CALCULATING..."): R does nothing then (Ben's Hurston try, #682)."""
    picked: bool = False
    clicked_row: int = -1

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
            self.sliding = self.slides if self.map_open else 0
            if self.map_open and self.seated and not self.route_id and not self.log_quiet:
                self._log(loglines.NO_ROUTE_ON_MAP)
        elif code == code_for("c") and self.map_open:
            self.route_id = ""
        elif code == code_for("r") and self.map_open and self.entered:
            if self.route_id or not self.fuel or (self.calculates and self.picked):
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
        if self.sliding:
            return ""  # the map is still opening: the click lands on nothing
        if abs(x - BOX_MIDDLE[0]) < 20 and abs(y - BOX_MIDDLE[1]) < 20:
            self.box_has_keys = True
        else:
            on_name = self.details and abs(x - TITLE[0]) < 50 and abs(y - TITLE[1]) < 20
            on_name = on_name or (
                self.empties and abs(x - PICKED[0]) < 50 and abs(y - PICKED[1]) < 20
            )
            on_x = abs(x - self.close_at[0]) < 20 and abs(y - self.close_at[1]) < 20
            at = round((y - ROW_TOP - 10) / ROW)
            if self.rows and not self.entered and 0 <= at < len(self.rows):
                self.clicked_row, self.entered, self.picked = at, True, True
                self.box_has_keys = self.rows[at] != self.typed.upper()  # another row: no
            if on_name or on_x:  # a click outside the box gives the keys back to the map
                self.box_has_keys = False
                self.picked = self.picked or on_name
        return ""

    # --- what the screen shows ---
    def read(self) -> Reading:
        if self.calculates and self.picked:
            self.calculates -= 1
            return Reading(WIDTH, HEIGHT, (Word("CALCULATIN@...", 500, 900, 200, 20, 60),), 7)
        if not self.map_open:
            return Reading(WIDTH, HEIGHT, (Word("FUEL", 100, 900, 60, 20, 0),), window=7)
        hint = [] if self.box_has_keys and not self.keeps_hint else ["SEARCH", "LOCATIONS..."]
        box = (self.typed.upper().split() if self.reads_letters else []) or hint
        left = BOX[0] - 40 * self.sliding
        self.sliding = max(0, self.sliding - 1)
        words = [Word(text, left + 70 * i, BOX[1], 60, BOX[3], 0) for i, text in enumerate(box)]
        if not self.typed:
            for i, place in enumerate(("RECENT", *(p.upper() for p in self.recent))):
                words.append(Word(place, BOX[0], 220 + 40 * i, 90, 20, 10 + i))
        elif not self.entered:  # the list filters as letters land
            words.append(Word("RESULTS", BOX[0], 220, 90, 20, 1))
            for i, text in enumerate(self.rows):
                words.append(Word(text, BOX[0] + 20, ROW_TOP + ROW * i, 160, 20, 70 + i))
        if self.empties and self.entered and self.typed in self.places:
            words = [
                Word(t, BOX[0] + 70 * i, BOX[1], 60, BOX[3], 0)
                for i, t in enumerate(("SEARCH", "LOCATIONS..."))
            ]
            for i, text in enumerate(("RECENT", "STANTON SYSTEM", "LEVSKJ", "ALLRESULTS")):
                words.append(
                    Word(text, BOX[0] + (20 if i == 2 else 0), 220 + 35 * i, 90, 20, 40 + i)
                )
            words.append(Word("AREA18", BOX[0] + 20, 370, 90, 20, 50))
            words.append(Word("LORVILLE", BOX[0] + 20, 400, 90, 20, 51))
            return Reading(WIDTH, HEIGHT, tuple(words), window=7)
        if self.details and self.entered and self.typed in self.places:
            words.append(Word(self.typed.upper(), BOX[0], 220, 90, 20, 30))  # its panel
            words.append(Word("ROUTE", BOX[0], 600, 60, 20, 31))
            return Reading(WIDTH, HEIGHT, tuple(words), window=7)
        if (self.box_has_keys or self.entered) and self.shows_x:
            words.append(Word("x", self.close_at[0] - 6, self.close_at[1] - 8, 12, 16, 0))
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
    flow: RouteFlow = FLOW,
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
    place = options.pop("place", Place(name))
    return plot(place, flow, **options)  # type: ignore[arg-type]


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


def test_a_map_still_opening_is_clicked_only_once_its_box_sits_still() -> None:
    game = Game(slides=2)  # Ben's PC try, 25 Nov 2026: the first click came too soon
    done = _plot(game)
    assert done.status == "done"
    assert game.clicks == [BOX_MIDDLE, CLOSE]


def test_one_bad_look_while_the_map_settles_is_only_one_look() -> None:
    game = Game(slides=1)

    class Blinks:
        """Text recognition misses one frame as the map settles (review of #670)."""

        looks = 0

        def read(self) -> Reading:
            self.looks += 1
            if self.looks == 2:
                raise CaptureError("a frame was late")
            return game.read()

    done = _plot(game, reader=Blinks())
    assert done.status == "done"
    assert game.clicks == [BOX_MIDDLE, CLOSE]


def test_a_box_with_the_keys_drops_its_empty_words_and_still_gets_the_name() -> None:
    game = Game()  # Ben's PC try, 26 Nov 2026: the click took, and the empty words went
    done = _plot(game)
    assert done.status == "done"
    assert game.route_id == "levski_all-001"


def test_a_box_that_keeps_its_empty_words_until_a_letter_still_gets_the_name() -> None:
    game = Game(keeps_hint=True)  # as on 24 Nov
    assert _plot(game).status == "done"


def test_a_map_that_closed_after_the_click_shows_neither_and_gets_no_letters() -> None:
    game = Game()

    class Closes:
        looks = 0

        def read(self) -> Reading:
            self.looks += 1
            if game.box_has_keys:  # the map closed just after the click
                return Reading(WIDTH, HEIGHT, (Word("FUEL", 100, 900, 60, 20, 0),), window=7)
            return game.read()

    done = _plot(game, reader=Closes())
    assert done.detail == "the map closed before typing"
    assert game.typed == ""
    assert not done.typed


def test_a_first_letter_text_recognition_cant_read_still_counts_once_recent_goes() -> None:
    game = Game(reads_letters=False)  # only the list under the box changes
    done = _plot(game)
    assert done.detail == "the search box doesn't show the name"  # Enter still waits
    assert game.typed == "levski"
    assert not game.entered


def test_a_caret_read_as_i_is_not_a_letter() -> None:
    game = Game(takes_keys=False)  # review of #672: the box shows a caret, takes nothing
    real = game.read

    def read() -> Reading:
        reading = real()
        if game.stray:  # after the first letter went astray, the caret blinks on
            caret = Word("I", BOX[0], BOX[1], 8, BOX[3], 0)
            return Reading(reading.width, reading.height, (*reading.words, caret), 7)
        return reading

    class Reader:
        def read(self) -> Reading:
            return read()

    done = _plot(game, reader=Reader())
    assert done.detail == "the search box didn't take the first letter"
    assert game.stray == ["l"]


def test_a_map_that_closes_on_the_first_letter_gets_no_more() -> None:
    game = Game()  # review of #672: no RECENT, but no panel either

    class Closes:
        def read(self) -> Reading:
            if game.typed:
                return Reading(WIDTH, HEIGHT, (Word("FUEL", 100, 900, 60, 20, 0),), window=7)
            return game.read()

    done = _plot(game, reader=Closes())
    assert done.detail == "the search box didn't take the first letter"
    assert game.typed == "l"


def test_the_x_read_on_the_box_line_is_clicked_wherever_it_is() -> None:
    near = (BOX_MIDDLE[0] + 300, BOX_MIDDLE[1])  # nearer than the offset, as at 5120x2160
    game = Game(close_at=near)
    assert _plot(game).status == "done"
    assert game.clicks == [BOX_MIDDLE, near]


def test_an_x_off_the_box_line_is_clicked_where_the_offset_puts_it() -> None:
    game = Game(close_at=(CLOSE[0], CLOSE[1] + 25))  # read a little low, off the line
    done = _plot(game)
    assert game.clicks == [BOX_MIDDLE, CLOSE]
    assert done.status == "failed"  # the click missed the x drawn lower: R isn't pressed


def test_the_places_own_panel_after_enter_is_chosen_and_its_name_clicked() -> None:
    game = Game(details=True)  # Ben's PC try, 26 Nov 2026: no results list, no x
    done = _plot(game)
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicks == [BOX_MIDDLE, TITLE]
    assert "click on the place's name in the panel" in done.pressed


def test_a_box_enter_emptied_takes_the_place_under_recent_and_clicks_it() -> None:
    game = Game(empties=True)  # Ben's Orison try, 26 Nov 2026: "ORISOW" under RECENT
    done = _plot(game)
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicks == [BOX_MIDDLE, PICKED]
    assert "click on the place's name under recent" in done.pressed


def test_a_box_enter_left_blank_still_takes_the_place_under_recent() -> None:
    game = Game(empties=True)  # Ben's Hurston try, 26 Nov 2026: no words in the box at all
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            if not game.entered:
                return reading
            kept = [w for w in reading.words if w.text not in ("SEARCH", "LOCATIONS...")]
            return Reading(reading.width, reading.height, tuple(kept), reading.window)

    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicks == [BOX_MIDDLE, PICKED]


def test_a_map_already_open_is_closed_and_opened_again_not_just_closed() -> None:
    game = Game(map_open=True)  # Ben's Hurston try, 26 Nov 2026: the map key closed it
    done = _plot(game)
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert done.pressed[:3] == ["v_starmap", "v_master_mode_set_nav", "v_starmap"]


def test_the_exact_row_is_clicked_instead_of_enter() -> None:
    game = Game(rows=("STANTON SYSTEM", "LEVSKI DEPOT", "LEVSKI"))  # MicroTech's depot, #682
    done = _plot(game)
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicked_row == 2
    assert "ui_textfield_enter" not in done.pressed
    assert "click on the place's name in the results" in done.pressed


def test_a_route_to_the_planets_number_is_to_the_planet() -> None:
    game = Game(places={"bloom": "pyro3"})  # Ben's Pyro try, 27 Nov 2026
    done = _plot(game, "Bloom", name_of={"pyro3": "Bloom"}.get)
    assert (done.status, done.say) == ("done", "Route set to Bloom.")


def test_a_route_to_another_planets_number_is_not_claimed() -> None:
    game = Game(places={"bloom": "pyro2"})
    done = _plot(game, "Bloom", name_of={"pyro2": "Monox"}.get)
    assert (done.status, done.say) == ("failed", LINES.other_place.format(name="Bloom"))


def _box_holding(text: str, *under: str) -> Reading:
    words = [Word(text, BOX[0], BOX[1], 90, BOX[3], 0)]
    words += [Word(w, BOX[0] + 60 * i, BOX[1] + 30, 50, BOX[3], 1) for i, w in enumerate(under)]
    return Reading(WIDTH, HEIGHT, tuple(words), 7)


def test_a_map_open_with_an_old_name_in_its_box_is_left_alone() -> None:
    class Reader:  # Ben's Terminus tries, 27 Nov 2026: "TERMINNUS" over NO RESULTS
        def read(self) -> Reading:
            return _box_holding("TERMINNUS", "NO", "RESULTS")

    game = Game()
    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("refused", LINES.box_full)
    assert done.pressed == []


def test_a_map_that_opens_with_an_old_name_in_its_box_says_so() -> None:
    game = Game()
    real = game.read

    class Reader:  # closed at first; once open, its box still holds the old name
        def read(self) -> Reading:
            reading = real()
            if not game.map_open:
                return reading
            return _box_holding("TERMINNUS", "NO", "RESULTS")

    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("failed", LINES.box_full)
    assert game.typed == ""
    assert game.clicks == []


def test_a_box_misread_still_picks_the_exact_row() -> None:
    game = Game(rows=("STANTON SYSTEM", "LEVSKI"))  # "TERMINI-JS" for Terminus, 27 Nov
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            misread = tuple(
                replace(w, text="LFVSKJ") if w.text == "LEVSKI" and w.top == BOX[1] else w
                for w in reading.words
            )
            return Reading(reading.width, reading.height, misread, reading.window)

    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicked_row == 1


def test_the_row_in_the_players_system_is_clicked() -> None:
    game = Game(rows=("NYX SYSTEM", "LEVSKI", "STANTON SYSTEM", "LEVSKI"))  # Pyro Gateway
    done = _plot(game, "Levski", place=Place("Levski", "Stanton"))
    assert done.status == "done"
    assert game.clicked_row == 3


def test_a_place_only_in_another_systems_section_falls_back_to_enter() -> None:
    game = Game(rows=("NYX SYSTEM", "LEVSKI", "STANTON SYSTEM", "LEVSKI DEPOT"))
    done = _plot(game, place=Place("Levski", "Stanton"))
    assert game.clicked_row == -1
    assert "ui_textfield_enter" in done.pressed


def test_a_system_the_list_has_no_section_for_is_not_trusted() -> None:
    game = Game(rows=("PYRO SYSTEM", "LEVSKI"))  # review of #686: the log still says Stanton
    done = _plot(game, place=Place("Levski", "Stanton"))
    assert done.status == "done"
    assert game.clicked_row == 1


def test_a_pick_in_another_system_is_said_and_no_r_pressed() -> None:
    game = Game(details=True)  # Ben's Pyro Gateway try, #682: "TARGET IS IN ANOTHER SYSTEM"
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            if not game.picked:
                return reading
            said = ("TARGET", "IS", "IN", "ANOTHERSYSTEM")  # run together, as OCR may
            far = tuple(Word(w, 300 + 90 * i, 900, 80, 20, 80) for i, w in enumerate(said))
            return Reading(reading.width, reading.height, reading.words + far, 7)

    flow = replace(FLOW, calculating="calculatin", other_system="another system")
    done = _plot(game, reader=Reader(), flow=flow)
    assert done.status == "failed"
    assert done.say == "The map picked a Levski in another system, so I left it open for you."
    assert "mapui_action_planroute" not in done.pressed


def test_another_place_under_recent_is_not_taken() -> None:
    game = Game(empties=True, places={"levski": "levski_all-001", "lorville": "lorville-001"})
    done = _plot(game, "Lorville")  # RECENT still shows Levski, ALL RESULTS the rest
    assert done.status == "failed"
    assert game.clicks == [BOX_MIDDLE]
    assert not game.route_id


def test_a_place_listed_far_under_recent_is_not_taken_when_all_results_goes_unread() -> None:
    game = Game(empties=True, places={"levski": "levski_all-001", "lorville": "lorville-001"})
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            if not game.entered:
                return reading
            kept = [w for w in reading.words if w.text not in ("ALLRESULTS", "LORVILLE")]
            kept.append(Word("LORVILLE", BOX[0] + 20, 500, 90, 20, 51))  # far down the list
            return Reading(reading.width, reading.height, tuple(kept), reading.window)

    done = _plot(game, "Lorville", reader=Reader())
    assert done.status == "failed"
    assert game.clicks == [BOX_MIDDLE]


def test_no_results_read_as_one_word_is_still_no_results() -> None:
    # Review of #699 (#704): "NORESULTS" passed for the results header, and the player
    # heard "The map didn't list" instead of why.
    game = Game()
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            words = [w for w in reading.words if w.text not in ("NO", "RESULTS")]
            if len(words) < len(reading.words):
                words.append(Word("NORESULTS", BOX[0], 220, 120, 20, 1))
            return Reading(reading.width, reading.height, tuple(words), reading.window)

    done = _plot(game, "Port Tressler", reader=Reader())
    assert done.say == "The map can't find Port Tressler. It only searches the system you're in."
    assert game.clicks == [BOX_MIDDLE]
    assert heading(_lines("NORESULTS"), "results", "no results") is None
    assert heading(_lines("NORESULTS"), "results") is not None  # as before, with no "unless"


def test_no_results_is_said_even_with_the_place_under_recent() -> None:
    game = Game(empties=True)
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            if not game.entered:
                return reading
            nothing = (
                Word("NO", BOX[0], 470, 40, 20, 60),
                Word("RESULTS", BOX[0] + 50, 470, 90, 20, 60),
            )
            return Reading(
                reading.width, reading.height, (*reading.words, *nothing), reading.window
            )

    done = _plot(game, reader=Reader())
    assert (done.status, done.detail) == ("failed", "the map found nothing")
    assert game.clicks == [BOX_MIDDLE]


def test_the_name_only_inside_a_description_is_not_the_places_panel() -> None:
    game = Game(details=True)
    real = game.read

    def read() -> Reading:
        reading = real()
        if not game.entered:
            return reading
        kept = tuple(w for w in reading.words if w.line != 30)  # no title line
        text = (
            Word("evolved,", BOX[0], 230, 90, 20, 40),
            Word("LEVSKI", BOX[0] + 100, 230, 90, 20, 40),
        )
        return Reading(reading.width, reading.height, (*kept, *text), reading.window)

    class Reader:
        def read(self) -> Reading:
            return read()

    done = _plot(game, reader=Reader())
    assert done.detail == "the frame doesn't show the search results"
    assert game.clicks == [BOX_MIDDLE]
    assert not game.route_id


def test_recent_far_down_in_a_description_doesnt_hide_the_places_panel() -> None:
    game = Game(details=True)
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            if not game.entered:
                return reading
            far = Word("recent", BOX[0], 700, 90, 20, 50)  # "...its recent history..."
            return Reading(reading.width, reading.height, (*reading.words, far), reading.window)

    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicks == [BOX_MIDDLE, TITLE]


def test_a_box_that_never_sits_still_is_never_clicked() -> None:
    game = Game(slides=50)
    done = _plot(game)
    assert (done.status, done.say) == ("failed", "I couldn't open the star map.")
    assert done.detail == "the map's search box didn't sit still"
    assert game.clicks == []
    assert game.typed == ""


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
    # If the box didn't take the keys after all, "l" would eject. A box that still shows
    # its empty words after the click gives no sign it has them (#682).
    game = Game(keeps_hint=True, shows_x=False)
    real = game.read

    class Reader:  # no RECENT list either: nothing shows the box has the keys
        def read(self) -> Reading:
            reading = real()
            kept = tuple(w for w in reading.words if w.text != "RECENT")
            return Reading(reading.width, reading.height, kept, reading.window)

    keybinds = Keybinds([*KEYBINDS, _bound("v_eject", "l", "spaceship_general")])
    done = _plot(game, keybinds=keybinds, reader=Reader())
    assert done.status == "refused"
    assert not any(p.startswith("typed") for p in done.pressed)
    assert game.typed == ""
    assert game.stray == []
    assert not game.map_open  # closed again, as the on-foot refusal does (review of #726)


def test_a_box_without_its_empty_words_but_no_x_or_list_gives_no_sign_it_has_the_keys() -> None:
    box = (Word("LEVSKI", BOX[0], BOX[1], 60, BOX[3], 0),)  # the empty words gone, no x
    assert not focused(Reading(WIDTH, HEIGHT, box, 7), box, FLOW)
    x = Word("x", BOX[0] + 80, BOX[1], 8, BOX[3], 0)
    assert focused(Reading(WIDTH, HEIGHT, (*box, x), 7), box, FLOW)


def test_a_dangerous_first_letter_is_typed_once_the_box_shows_it_has_the_keys() -> None:
    game = Game()  # "Bloom" with Change Fire Mode on B (Ben's Pyro try, 27 Nov 2026)
    keybinds = Keybinds([*KEYBINDS, _bound("v_eject", "l", "spaceship_general")])
    done = _plot(game, keybinds=keybinds)
    assert (done.status, done.say) == ("done", "Route set to Levski.")


def test_a_dangerous_key_past_the_first_letter_waits_for_the_box_and_is_typed() -> None:
    game = Game()  # Ben's Aberdeen try, #682: Change Fire Mode on "b"
    keybinds = Keybinds([*KEYBINDS, _bound("v_eject", "v", "spaceship_general")])
    done = _plot(game, keybinds=keybinds)
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert "typed Levski" in done.pressed


def test_a_dangerous_first_letter_is_said_by_its_label_never_its_id() -> None:
    def unsure() -> Game:  # the box keeps its empty words: no sign it has the keys
        return Game(keeps_hint=True, shows_x=False, recent=())

    eject = replace(_bound("v_eject", "l", "spaceship_general"), label="Eject")
    said = "I can't type Levski: its first letter is your Eject key."
    assert _plot(unsure(), keybinds=Keybinds([*KEYBINDS, eject])).say == said
    unlabelled = Keybinds([*KEYBINDS, _bound("v_eject", "l", "spaceship_general")])
    assert _plot(unsure(), keybinds=unlabelled).say == said  # in words (#705)


def test_a_later_letter_on_a_dangerous_key_waits_for_a_look_at_the_box() -> None:
    game = Game()  # review of #685: the box stops taking keys after the first letter
    keybinds = Keybinds([*KEYBINDS, _bound("v_eject", "v", "spaceship_general")])
    real = game.read

    class Reader:
        def read(self) -> Reading:
            if game.typed == "l":
                game.takes_keys = False
            return real()

    done = _plot(game, keybinds=keybinds, reader=Reader())
    assert done.detail == "the box lost the keys"
    assert game.stray == ["e"]  # never the "v"


def test_r_waits_while_the_map_works_out_the_fuel() -> None:
    game = Game(details=True, calculates=3)  # Ben's Hurston try, #682
    done = _plot(game, flow=replace(FLOW, calculating="calculatin"))
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.calculates == 0


def test_r_waits_for_a_fuel_sum_that_starts_a_look_late() -> None:
    game = Game(details=True, calculates=2)  # review of #685
    real, late = game.read, [True]

    class Reader:
        def read(self) -> Reading:
            if game.picked and late[0]:  # the first look after the pick: not started yet
                late[0], held, game.calculates = False, game.calculates, 0
                reading, game.calculates = real(), held
                return reading
            return real()

    done = _plot(game, reader=Reader(), flow=replace(FLOW, calculating="calculatin"))
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.calculates == 0


def test_a_fuel_sum_that_never_ends_presses_no_r() -> None:
    game = Game(details=True, calculates=10_000)
    flow = replace(FLOW, calculating="calculatin", calculating_seconds=2.0)
    done = _plot(game, flow=flow)
    assert done.status == "failed"
    assert "mapui_action_planroute" not in done.pressed
    assert not game.route_id


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
    assert (done.status, done.say) == ("failed", LINES.mistyped.format(name="Levski"))
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


def test_one_misread_letter_is_allowed_only_in_longer_names_and_never_a_digit() -> None:
    assert alike("ORISOW", "Orison")
    assert alike("Levski", "Levski")
    assert not alike("ARC-L2", "ARC-L1")  # a digit is another place
    assert not alike("HURSTOM", "Hurstonx")  # not the same length
    assert not alike("YELA", "Yeli")  # too short to guess
    assert not alike("ORISAW", "Orison")  # two letters off
    assert not alike("PYRO V", "Pyro I")  # a numbered planet is another place
    assert not alike("PYRO IV", "Pyro II")


def test_recent_skips_a_place_in_another_systems_section() -> None:
    # Ben's Pyro Gateway try, #682: RECENT kept the Nyx-side gateway from his trip.
    lines = ("RECENT", "NYX SYSTEM", "PYRO GATEWAY", "STANTON SYSTEM", "HURSTON")
    words = tuple(
        Word(word, 100 + 80 * j, 100 + 30 * i, 70, 20, i)
        for i, line in enumerate(lines)
        for j, word in enumerate(line.split())
    )
    reading = Reading(WIDTH, HEIGHT, words, 7)
    assert under_header(reading, "recent", "results", "Pyro Gateway", 0.5, "Stanton") is None
    assert under_header(reading, "recent", "results", "Pyro Gateway", 0.5, None) is not None
    assert under_header(reading, "recent", "results", "Hurston", 0.5, "Stanton") is not None


def test_a_modifier_held_at_a_click_clicks_nothing_and_says_to_let_go() -> None:
    """#678: the map's clicks went out as Ctrl + click while the player held Ctrl."""
    game = Game()
    sender = FakeKeySender()
    sender.on_event = lambda: _key_went_down(sender, game)
    clock = FakeClock()
    presser = Presser(sender, FakeFocus(True), sleep=clock.sleep, clock=clock, game="SC")

    class HeldAtClick:
        def press(self, press: object, cancel: threading.Event | None = None) -> object:
            return presser.press(press, cancel)  # type: ignore[arg-type]

        def letting_go(self, cancel: threading.Event | None = None) -> tuple[str, bool]:
            return "Let go of Ctrl and say it again.", True

    done = _plot(game, presser=HeldAtClick())
    assert (done.status, done.say) == ("modifier_held", "Let go of Ctrl and say it again.")
    assert game.clicks == []
    assert not game.route_id


def _lines(*lines: str) -> Reading:
    words = tuple(
        Word(word, 100 + 80 * j, 100 + 30 * i, 70, 20, i)
        for i, line in enumerate(lines)
        for j, word in enumerate(line.split())
    )
    return Reading(WIDTH, HEIGHT, words, 7)


def test_a_misread_results_header_still_heads_the_list() -> None:
    # Ben's Hurston try, #682: text recognition read "ALL RESULTS" as "ALURESULT$" (#684).
    reading = _lines("ALURESULT$", "STANTON SYSTEM", "HURSTON")
    assert under_header(reading, "results", "", "Hurston", 0.5, "Stanton") is not None
    assert (
        under_header(
            _lines("RECENT", "ALURESULT$", "HURSTON"), "recent", "results", "Hurston", 0.5, None
        )
        is None
    )


def test_a_header_is_only_matched_loosely_in_one_short_word() -> None:
    assert heading(_lines("ALL RESULTS (2)"), "results") is not None
    assert heading(_lines("ALLRE5ULTS"), "results") is not None
    assert heading(_lines("RESULT"), "results") is None  # a letter short
    assert heading(_lines("NORESULTSWEREFOUNDHERE"), "results") is None  # too long to be it
    assert heading(_lines("HURSTON"), "results") is None
    assert heading(_lines("ANYTHING"), "") is None


class _HeldAtClicks(FakeKeySender):
    """The player holds Ctrl for ``looks`` looks once ``when`` holds (the map is open:
    Tycho is about to click, #705); ``then`` runs at the first, as the panel may change
    in that wait."""

    def __init__(
        self,
        game: Game,
        looks: int,
        then: Callable[[], None] = lambda: None,
        when: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self.left, self.then, self.waiting = looks, then, False
        self.when = when or (lambda: game.map_open)
        self.on_event = lambda: _key_went_down(self, game)

    def held_modifiers(self) -> frozenset[Code]:
        self.looks += 1
        if self.left <= 0 or not self.when():
            return frozenset()
        if not self.waiting:
            self.waiting = True
            self.then()
        self.left -= 1
        return frozenset({code_for("lctrl")})


def _plot_held(game: Game, sender: FakeKeySender, cancel: threading.Event | None = None) -> Plotted:
    clock = FakeClock()
    presser = Presser(sender, FakeFocus(True), sleep=clock.sleep, clock=clock, game="SC")
    return _plot(game, presser=presser, cancel=cancel or threading.Event())


def test_ctrl_let_go_in_time_at_a_click_still_plots_with_a_real_presser() -> None:
    game = Game()  # review of #700: end to end, Ctrl down only when the click comes
    done = _plot_held(game, _HeldAtClicks(game, looks=3))
    assert (done.status, done.say) == ("done", "Route set to Levski.")
    assert game.clicks == [BOX_MIDDLE, CLOSE]


def test_ctrl_held_through_the_wait_at_a_click_clicks_nothing() -> None:
    game = Game()
    done = _plot_held(game, _HeldAtClicks(game, looks=1000))
    assert (done.status, done.say) == ("modifier_held", "Let go of Ctrl and say it again.")
    assert game.clicks == []


def test_a_cancel_while_waiting_for_ctrl_clicks_nothing() -> None:
    game, cancel = Game(), threading.Event()
    done = _plot_held(game, _HeldAtClicks(game, looks=1000, then=cancel.set), cancel=cancel)
    assert done.status == "cancelled"
    assert game.clicks == []


def test_a_click_aimed_before_a_modifier_wait_looks_again_first() -> None:
    # Review of #700: the panel moved while Tycho waited for Ctrl to be let go, and the
    # click was aimed from the frame read before the wait.
    game = Game()

    def moved() -> None:
        game.close_at = (CLOSE[0] + 200, CLOSE[1] + 100)

    sender = _HeldAtClicks(game, looks=3, then=moved, when=lambda: game.entered)
    done = _plot_held(game, sender)
    assert (done.status, done.detail) == (
        "failed",
        "the search panel's close button changed in the wait",
    )
    assert game.clicks == [BOX_MIDDLE]


def test_a_box_read_one_letter_wrong_still_shows_the_name() -> None:
    game = Game()  # Ben's Pyro try, 27 Nov 2026: Orbituary in the box read "ORBITUARV"
    real = game.read

    class Reader:
        def read(self) -> Reading:
            reading = real()
            misread = tuple(
                replace(w, text="LEVSKJ") if w.text == "LEVSKI" and w.top == BOX[1] else w
                for w in reading.words
            )
            return Reading(reading.width, reading.height, misread, reading.window)

    done = _plot(game, reader=Reader())
    assert (done.status, done.say) == ("done", "Route set to Levski.")


@pytest.mark.parametrize("read", ["LEVSK", "LEVSK1", "LAVSKJ"])
def test_a_box_short_a_letter_or_a_digit_off_does_not_show_the_name(read: str) -> None:
    box = (Word(read, BOX[0], BOX[1], 60, BOX[3], 0), Word("x", BOX[0] + 80, BOX[1], 8, BOX[3], 0))
    assert not shows_alike(Reading(WIDTH, HEIGHT, box, 7), "Levski", FLOW)  # x left out
