"""Which screen the Tile shows on (#447, ADR 16 amended): tested with no screen."""

from typing import Any

import pytest

from tycho.settings import Settings
from tycho.tile import Card, screens
from tycho.tile.display import SCREEN_MISSING, TileDisplay
from tycho.tile.screens import GAME, Connected, Where, chosen, named, ordered
from tycho.tile.window import SAME, Screen

MAIN = Connected(r"\\.\DISPLAY1", Screen(0, 0, 2560, 1440), main=True)
LEFT = Connected(r"\\.\DISPLAY3", Screen(-1920, 0, 0, 1080))
RIGHT = Connected(r"\\.\DISPLAY2", Screen(2560, 0, 4480, 1080))
GAMES = Screen(0, 0, 2560, 1440)
"""The screen the window in front is on."""


def test_screens_are_listed_main_first_then_left_to_right() -> None:
    assert ordered([RIGHT, LEFT, MAIN]) == [MAIN, LEFT, RIGHT]
    assert named([RIGHT, MAIN]) == [
        {"value": r"\\.\DISPLAY1", "name": "Screen 1 (main), 2560 x 1440"},
        {"value": r"\\.\DISPLAY2", "name": "Screen 2, 1920 x 1080"},
    ]


def test_a_chosen_screen_is_found_by_windows_name_for_it() -> None:
    assert chosen(r"\\.\DISPLAY2", [MAIN, RIGHT]) == RIGHT.screen
    assert chosen(GAME, [MAIN, RIGHT]) is None
    assert chosen(r"\\.\DISPLAY9", [MAIN, RIGHT]) is None


class Rig:
    def __init__(self, choice: str, plugged: list[Connected]) -> None:
        self.choice = choice
        self.plugged = plugged
        self.now = 0.0
        self.missing: list[str] = []
        self.listed = 0
        self.where = Where(
            lambda: self.choice,
            foreground=lambda: GAMES,
            connected=self._connected,
            on_missing=self._told,
            clock=lambda: self.now,
        )

    def _told(self, choice: str) -> bool:
        self.missing.append(choice)
        return True

    def _connected(self) -> list[Connected]:
        self.listed += 1
        return list(self.plugged)


def test_following_the_game_is_the_default_and_asks_windows_nothing() -> None:
    assert Settings().tile_screen == GAME
    rig = Rig(GAME, [MAIN, RIGHT])
    assert rig.where() == GAMES
    assert rig.listed == 0


def test_the_tile_stays_on_the_chosen_screen() -> None:
    rig = Rig(r"\\.\DISPLAY2", [MAIN, RIGHT])
    assert rig.where() == RIGHT.screen
    assert rig.where() == RIGHT.screen
    assert rig.listed == 1  # kept between frames
    rig.now += screens.KEEP_SECONDS + 0.1
    rig.where()
    assert rig.listed == 2


def test_an_unplugged_screen_follows_the_game_and_says_so_once() -> None:
    rig = Rig(r"\\.\DISPLAY2", [MAIN])
    assert rig.where() == GAMES
    assert rig.where() == GAMES
    assert rig.missing == [r"\\.\DISPLAY2"]
    # Plugged in again: back there; unplugged again: said again.
    rig.plugged, rig.now = [MAIN, RIGHT], 10.0
    assert rig.where() == RIGHT.screen
    rig.plugged, rig.now = [MAIN], 20.0
    assert rig.where() == GAMES
    assert rig.missing == [r"\\.\DISPLAY2", r"\\.\DISPLAY2"]


def test_a_screen_list_that_fails_follows_the_game() -> None:
    def failing() -> list[Connected]:
        raise OSError("no")

    told: list[str] = []
    where = Where(
        lambda: r"\\.\DISPLAY2", foreground=lambda: GAMES, connected=failing, on_missing=told.append
    )
    assert where() == GAMES
    assert told == []  # don't know isn't "unplugged" (#468 review)


def test_a_missing_screen_is_said_once_the_player_can_see_it() -> None:
    """#468 review: while the Tile is off or notices are hidden, saying it fails, so
    it's tried again until the player is told."""
    told: list[str] = []
    seen = False

    def say(choice: str) -> bool:
        told.append(choice)
        return seen

    where = Where(
        lambda: r"\\.\DISPLAY2", foreground=lambda: GAMES, connected=lambda: [MAIN], on_missing=say
    )
    where()
    where()
    assert len(told) == 2
    seen = True
    where()
    where()
    assert len(told) == 3


@pytest.mark.parametrize(
    ("given", "saved"), [(" Game ", "game"), ("", "game"), (r"\\.\DISPLAY2", r"\\.\DISPLAY2")]
)
def test_the_setting_is_game_or_a_screen_windows_names(given: str, saved: str) -> None:
    assert Settings(tile_screen=given).tile_screen == saved


def test_a_screen_that_isnt_windows_name_is_refused() -> None:
    with pytest.raises(ValueError, match="as Windows names it"):
        Settings(tile_screen="DISPLAY2")


class FakeWindow:
    def __init__(self, frame: Any) -> None:
        self.frame = frame
        self.failed = ""
        self.where: Any = None

    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass


def test_the_display_tells_the_window_where_and_says_a_missing_screen_on_the_tile() -> None:
    display = TileDisplay(
        Settings(tile_screen=r"\\.\DISPLAY2"),
        clock=lambda: 0.0,
        window=FakeWindow,  # type: ignore[arg-type]
    )
    window: FakeWindow = display._window  # type: ignore[assignment] # pyright: ignore[reportPrivateUsage]
    assert isinstance(window.where, Where)
    assert display._screen_missing(r"\\.\DISPLAY2")  # pyright: ignore[reportPrivateUsage]
    shown = display._board.view()  # pyright: ignore[reportPrivateUsage]
    assert any(SCREEN_MISSING in str(s) for s in shown)


@pytest.mark.parametrize("settings", [{"tile": False}, {"tile_hidden_cards": "notice"}])
def test_a_missing_screen_isnt_said_where_it_cant_be_seen(settings: dict[str, object]) -> None:
    """#468 review: not saved up while the Tile is off, and not counted as told when
    notices are hidden."""
    display = TileDisplay(Settings(**settings), clock=lambda: 0.0, window=FakeWindow)  # type: ignore[arg-type]
    assert not display._screen_missing(r"\\.\DISPLAY2")  # pyright: ignore[reportPrivateUsage]
    assert not display._board.view()  # pyright: ignore[reportPrivateUsage]


def test_a_new_screen_under_a_still_card_draws_it_there() -> None:
    """#468 review: a pinned card moves when its screen is plugged in or out."""
    display = TileDisplay(Settings(), clock=lambda: 0.0, window=FakeWindow)  # type: ignore[arg-type]
    display.show(Card("notice", body=("Kept.",)), pinned=True)
    assert display.frame(GAMES) not in (None, SAME)
    assert display.frame(GAMES) is SAME
    assert display.frame(RIGHT.screen) not in (None, SAME)


def test_a_new_screen_shows_a_sample_there_in_plain_words() -> None:
    display = TileDisplay(Settings(), clock=lambda: 0.0, window=FakeWindow)  # type: ignore[arg-type]
    display.change("tile_screen", r"\\.\DISPLAY2")
    shown = str(display._board.view())  # pyright: ignore[reportPrivateUsage]
    assert "The Tile shows here now" in shown
    assert "DISPLAY" not in shown
