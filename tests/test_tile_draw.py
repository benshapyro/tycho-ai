"""Drawing the Tile (ADR 16), checked as pixels: no screen, no window."""

from typing import Any

import pytest
from PIL import Image

from tycho.tile import Board, Card, Row
from tycho.tile.cards import ALL_KINDS, BODY, confirm, long_answer, notice
from tycho.tile.design import CORE, accent_for
from tycho.tile.draw import MAX_CARD, WIDTH, Look, draw, rgba

LIST = Card(
    "list",
    title="Best places to sell",
    label="Sell · Laranite",
    source="UEX",
    rows=(Row("Everus Harbor", "Hurston", "3,048"), Row("Area18 TDD", "ArcCorp", "3,012")),
)


def _pixels(image: Image.Image) -> list[tuple[int, int, int, int]]:
    data = image.convert("RGBA").tobytes()
    return [(data[i], data[i + 1], data[i + 2], data[i + 3]) for i in range(0, len(data), 4)]


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _tile(card: Card | None = LIST, look: Look | None = None, **draw_args: Any) -> Image.Image:
    clock = Clock()
    board = Board(clock=clock)
    if card is not None:
        board.show(card)
    return draw(board.view(), look or Look(), now=draw_args.pop("now", clock.now), **draw_args)


def _near(image: Image.Image, colour: str, tolerance: int = 40) -> int:
    """How many pixels are close to ``colour`` and fully drawn."""
    r0, g0, b0, _ = rgba(colour)
    return sum(
        1
        for r, g, b, a in _pixels(image)
        if a > 200 and abs(r - r0) + abs(g - g0) + abs(b - b0) < tolerance
    )


def test_an_empty_tile_is_invisible() -> None:
    image = _tile(None)
    assert image.getchannel("A").getbbox() is None  # no pixel drawn at all


def test_the_glass_is_as_see_through_as_asked_and_text_never_fades() -> None:
    image = _tile(look=Look(see_through=20))
    alphas = [a for *_, a in _pixels(image)]
    glass = round(255 * 0.8)
    assert alphas.count(glass) > 1000  # the card's background
    assert max(alphas) == 255  # its text and lines
    clearer = _tile(look=Look(see_through=60))
    assert [a for *_, a in _pixels(clearer)].count(round(255 * 0.4)) > 1000


def test_high_contrast_is_solid() -> None:
    image = _tile(look=Look(see_through=60, high_contrast=True))
    alphas = [a for *_, a in _pixels(image)]
    assert alphas.count(255) > alphas.count(round(255 * 0.4))


def test_the_accent_follows_the_ship() -> None:
    drake = accent_for("DRAK_Cutlass_Black")
    assert _near(_tile(look=Look(accent=drake)), drake) > 50
    assert _near(_tile(), drake) == 0


def test_warnings_keep_their_own_colour_whatever_the_ship() -> None:
    warning = Card("warning", title="Lock all doors", body=("Keybindings, search lock all doors.",))
    red_ship = Look(accent=CORE.danger)
    assert _near(_tile(warning, red_ship), CORE.caution) > 20


@pytest.mark.parametrize("scale", [0.85, 1.0, 1.4])
def test_bigger_text_makes_a_bigger_tile(scale: float) -> None:
    assert _tile(look=Look(scale=scale)).width == round(WIDTH * scale)


def _card(kind: Any) -> Card:
    rows = (Row("Everus Harbor", "Hurston", "3,048"), Row("Area18", "Sell", "3,012"))
    if kind == "confirm":
        return confirm("Eject? Say confirm, Ace.", 5.0)
    if kind == "long":
        return long_answer("Quantum fuel is a tank of its own. " * 10)
    if kind == "notice":
        return notice("Speech recognition moved to the processor.")
    if kind == "warning":
        return Card(kind, title="Lock all doors", body=("Keybindings, search lock all doors.",))
    return Card(
        kind, title="A title", label="A label", source="UEX", rows=rows, hero=("Profit", "61,728")
    )


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize(
    "look", [Look(), Look(scale=1.4, high_contrast=True), Look(left=True, bottom=True)]
)
def test_every_kind_draws_in_every_look(kind: Any, look: Look) -> None:
    image = _tile(_card(kind), look)
    assert image.mode == "RGBA"
    assert image.height > 60


def test_a_long_answer_is_cut_to_the_tallest_card() -> None:
    image = _tile(long_answer("word " * BODY))
    assert image.height <= MAX_CARD + 40


def test_the_timer_bar_shrinks_as_a_card_fades() -> None:
    clock = Clock()
    board = Board(clock=clock)
    board.show(LIST)
    look = Look(accent="#ff00ff")  # an accent nothing else on the card is near

    def bar() -> int:
        image = draw(board.view(), look, now=clock.now)
        foot = image.crop((0, image.height - 1, image.width, image.height))
        return _near(foot, "#ff00ff")

    fresh = bar()
    clock.now += 4
    assert 0 < bar() < fresh


def test_the_countdown_empties_with_the_time_to_answer() -> None:
    clock = Clock()
    board = Board(clock=clock)
    board.show(confirm("Eject? Say confirm.", 5.0))

    def red() -> int:
        return _near(draw(board.view(), Look(), now=clock.now), CORE.danger)

    full = red()
    clock.now += 4
    assert red() < full


def test_the_talk_light_listening_is_green_and_thinking_is_the_accent() -> None:
    listening = _tile(None, light="listening")
    thinking = _tile(None, Look(accent="#ff00ff"), light="thinking", phase=0.0)
    assert _near(listening, CORE.good) > 10
    assert _near(thinking, "#ff00ff") > 10
    assert _near(listening, "#ff00ff") == 0


def test_the_talk_light_sits_by_the_corner() -> None:
    right = _tile(None, light="listening")
    left = _tile(None, Look(left=True), light="listening")
    assert right.getbbox() != left.getbbox()
    right_box, left_box = right.getbbox(), left.getbbox()
    assert right_box is not None
    assert left_box is not None
    assert right_box[0] > right.width // 2
    assert left_box[2] < left.width // 2


def test_in_a_bottom_corner_the_newest_card_is_nearest_the_corner() -> None:
    clock = Clock()
    board = Board(clock=clock, at_once=2)
    board.show(Card("notice", body=("Older.",)))
    board.show(LIST)
    top = draw(board.view(), Look(), now=clock.now)
    bottom = draw(board.view(), Look(bottom=True), now=clock.now)
    accent = Look().accent
    # The list card's accent-highlighted row is near the top in a top corner, and
    # near the bottom in a bottom corner.
    top_rows = _near(top.crop((0, 0, top.width, top.height // 2)), accent)
    bottom_rows = _near(bottom.crop((0, bottom.height // 2, bottom.width, bottom.height)), accent)
    assert top_rows > 20
    assert bottom_rows > 20


def test_a_strip_is_a_thin_line_with_the_title() -> None:
    clock = Clock()
    board = Board(clock=clock)
    board.show(LIST, pinned=True)
    board.show(Card("notice", body=("Newer.",)))
    strip = board.view()[1]
    assert strip.strip
    alone = draw((strip,), Look(), now=clock.now)
    assert alone.height < 70
