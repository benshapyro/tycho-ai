"""Drawing the Tile (ADR 16), checked as pixels: no screen, no window."""

from dataclasses import replace
from typing import Any

import pytest
from PIL import Image

from tycho.packs import STAR_CITIZEN, built_in
from tycho.tile import Board, Card, Row
from tycho.tile.cards import ALL_KINDS, BODY, confirm, long_answer, notice
from tycho.tile.design import CORE, accent_for
from tycho.tile.draw import MAX_CARD, UNANSWERED, WIDTH, Look, draw, rgba
from tycho.tile.faces import font, text_width, wrap
from tycho.tile.tables import bar_lengths, number, shared_unit, snug

SC = built_in(STAR_CITIZEN)

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
    drake = accent_for("DRAK_Cutlass_Black", tile=SC.tile)
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


def test_the_tile_draws_with_the_bundled_fonts() -> None:
    """The Tile draws with the same files as the window (ADR 17), not Windows' own."""
    from tycho.tile.faces import font

    assert font("label", 20).getname() == ("Saira Condensed", "SemiBold")
    assert font("text", 20).getname()[0] == "Saira SemiCondensed"
    assert font("strong", 20).getname() == ("Saira SemiCondensed", "SemiBold")
    assert font("number", 20).getname()[0] == "Share Tech Mono"


def test_every_symbol_the_tile_draws_is_in_its_font() -> None:
    """Review of #457: Share Tech Mono has no arrow, and Pillow doesn't fall back glyph by
    glyph, so a missing symbol drew as a box."""
    from PIL.ImageFont import FreeTypeFont

    from tycho.tile.faces import Face, font

    def drawn(f: FreeTypeFont, ch: str) -> bool:
        missing = "\U000f0000"  # a private-use code point: no font here has it
        return (f.getbbox(ch), f.getlength(ch)) != (f.getbbox(missing), f.getlength(missing))

    assert drawn(font("strong", 20), "→")
    faces: tuple[Face, ...] = ("label", "text", "strong", "number")
    for face in faces:
        for ch in "…·":
            assert drawn(font(face, 20), ch), (face, ch)


def test_greek_and_cyrillic_draw_in_windows_faces_not_boxes_and_the_euro_stays() -> None:
    """#458: the bundled fonts have no Greek or Cyrillic. A card with either draws in
    Windows' own faces, which have both, instead of as boxes. The € is in the bundled
    faces, so it keeps them (#515)."""
    import sys

    from tycho.tile.draw import _Pen  # pyright: ignore[reportPrivateUsage]
    from tycho.tile.faces import Face, bundled_draws, font

    assert bundled_draws("Sell · Laranite → Everus Harbor… Tiếng Việt €", "3,048 aUEC 12 €")
    for foreign in ("Ωμέγα", "Привет"):
        assert not bundled_draws(foreign), foreign
        assert not bundled_draws("", foreign), foreign
    assert not bundled_draws("", "→")  # Saira has the arrow; Share Tech Mono doesn't
    # A specs row with no value draws its detail in the number face (#515).
    arrow = Card("ship", title="Ship", rows=(Row("Route", "Area18 → Lorville", ""),))
    assert not bundled_draws(*arrow.shown())
    assert bundled_draws(*Card("list", title="Route", rows=(Row("Go", "A → B"),)).shown())
    look = Look()
    assert _Pen(LIST, look).windows is False
    greek = Card("list", title="Prices", rows=(Row("Αθήνα", "Ελλάδα", "12 €"), Row("Москва")))
    pen = _Pen(greek, look)
    assert pen.windows is True
    assert pen.font("text", 20) is not font("text", 20)
    assert _tile(greek).getbbox() is not None  # it draws
    if sys.platform == "win32":  # Windows' own faces are there to check
        missing = "\U000f0000"
        wanted: list[tuple[Face, str]] = [("text", "Ω"), ("strong", "Ж"), ("label", "Ж")]
        for face, ch in wanted:
            f = pen.font(face, 20)
            assert (f.getbbox(ch), f.getlength(ch)) != (f.getbbox(missing), f.getlength(missing))


def test_each_bundled_face_is_loaded_once_for_the_character_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#515: the first check of each character reloaded the font file, inside the
    Tile's lock. Now each face is loaded once, whatever characters are checked."""
    from tycho.tile import faces

    loads: list[str] = []
    real = faces._bundled  # pyright: ignore[reportPrivateUsage]

    def counted(face: faces.Face, size: int) -> faces.Font:
        loads.append(face)
        return real(face, size)

    monkeypatch.setattr(faces, "_bundled", counted)
    faces._has.cache_clear()  # pyright: ignore[reportPrivateUsage]
    faces._probe.cache_clear()  # pyright: ignore[reportPrivateUsage]
    try:
        assert faces.bundled_draws("Abc xyz", "123")
    finally:
        faces._has.cache_clear()  # pyright: ignore[reportPrivateUsage]
        faces._probe.cache_clear()  # pyright: ignore[reportPrivateUsage]
    assert sorted(loads) == ["label", "number", "strong", "text"]


# --- the table-like cards (#491) ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "found"),
    [
        ("3,048", (3048.0, "")),
        ("72 SCU", (72.0, "scu")),
        ("681.8 Gm", (681.8, "gm")),
        ("1 to 4", None),
        ("Large", None),
        ("", None),
        ("-1,200", (-1200.0, "")),
        ("\u22123.5 aUEC", (-3.5, "auec")),
        ("$1,200", (1200.0, "")),
        ("Nov 20", (20.0, "nov")),
        ("S3", (3.0, "s")),
    ],
)
def test_a_value_is_a_number_only_when_it_has_one(value: str, found: Any) -> None:
    assert number(value) == found


def test_bars_need_numbers_in_one_unit_that_differ() -> None:
    assert bar_lengths(["3,048", "1,524"]) == [1.0, 0.5]
    assert bar_lengths(["46 SCU", "66 SCU"]) == pytest.approx([46 / 66, 1.0])
    assert bar_lengths(["46 SCU", "66 m/s"]) is None  # different units
    assert bar_lengths(["3,048", "Large"]) is None
    assert bar_lengths(["3,048", "3,012"]) is None  # too close to say anything
    assert bar_lengths(["3,048"]) is None


def test_no_bars_for_a_loss_or_for_dates() -> None:
    """#527: "-1,200" isn't a bar as long as "1,200", and days of the month aren't bars."""
    assert bar_lengths(["1,200", "-1,200", "300"]) is None
    assert bar_lengths(["Nov 20", "Oct 3"]) is None


def _bar_pixels(rows: tuple[Row, ...]) -> int:
    """Accent pixels in the thin lines along the rows' feet: bars, not the best row."""
    card = Card("list", title="Sell", rows=rows)
    image = _tile(card, Look(accent="#f5901e"), now=100.0)
    return _near(image, "#f5901e", tolerance=140)


def test_a_list_draws_bars_when_its_numbers_differ() -> None:
    spread = (Row("A", value="3,048"), Row("B", value="1,500"), Row("C", value="900"))
    close = (Row("A", value="3,048"), Row("B", value="3,012"), Row("C", value="2,990"))
    assert _bar_pixels(spread) > _bar_pixels(close) + 200


def test_a_comparison_draws_its_left_side_in_the_accent() -> None:
    card = Card(
        "compare",
        title="For cargo",
        hero=("Cutlass Black", "Freelancer"),
        rows=(Row("Cargo", "46 SCU", "66 SCU"),),
    )
    plain = Card("compare", title="For cargo", hero=("", ""), rows=card.rows)
    look = Look(accent="#f5901e")
    assert _near(_tile(card, look), "#f5901e") > _near(_tile(plain, look), "#f5901e")


def test_a_loadout_names_each_slot_once_for_the_rows_that_share_it() -> None:
    one = Card("loadout", title="Cutlass", rows=(Row("Panther", "Weapons", "S3"),))
    two = Card(
        "loadout",
        title="Cutlass",
        rows=(Row("Panther", "Weapons", "S3"), Row("Panther", "Weapons", "S3")),
    )
    again = Card(
        "loadout",
        title="Cutlass",
        rows=(Row("Panther", "Weapons", "S3"), Row("Panther", "Turret", "S3")),
    )
    dim = CORE.dim
    assert _near(_tile(two), dim, 30) == pytest.approx(_near(_tile(one), dim, 30), rel=0.25)
    assert _near(_tile(again), dim, 30) > _near(_tile(two), dim, 30)


def test_only_a_kept_strip_has_the_pinned_marker() -> None:
    """#543: a strip that fades after a minute doesn't look like one that stays."""
    from tycho.tile.draw import strip_image

    card = Card("list", title="Best places to sell")
    fading, kept = strip_image(card, Look()), strip_image(card, Look(), kept=True)
    right = (fading.width - 40, 0, fading.width, fading.height)
    assert fading.crop(right).tobytes() != kept.crop(right).tobytes()
    left = (0, 0, fading.width - 40, fading.height)
    assert fading.crop(left).tobytes() == kept.crop(left).tobytes()


def test_a_list_shows_each_rows_second_line() -> None:
    """The Tile test, 26 Nov 2026: four rows all named "Derelict Outpost", and where each
    one is was never drawn."""
    bare = Card("list", title="Hadanite", rows=(Row("Derelict Outpost", "", "3.6%"),))
    told = Card("list", title="Hadanite", rows=(Row("Derelict Outpost", "Vuur, Pyro", "3.6%"),))
    assert _tile(told).height > _tile(bare).height
    assert _near(_tile(told), CORE.dim) > _near(_tile(bare), CORE.dim)


def test_a_list_says_a_shared_unit_once() -> None:
    rows = (Row("A", value="8,500 aUEC/SCU"), Row("B", value="7,700 aUEC/SCU"))
    assert shared_unit(rows) == (["8,500", "7,700"], "aUEC/SCU")
    assert shared_unit((Row("A", value="3.6%"), Row("B", value="2.8%"))) == (["3.6%", "2.8%"], "")
    mixed = (Row("A", value="72 SCU"), Row("B", value="Top pick"))
    assert shared_unit(mixed) == (["72 SCU", "Top pick"], "")
    assert shared_unit(rows[:1]) == (["8,500 aUEC/SCU"], "")
    assert shared_unit(()) == ([], "")


def test_steps_drop_their_second_lines_only_when_the_card_cant_hold_them() -> None:
    """By the room on the card, not a count: eight short steps keep theirs, eight long
    ones (two lines each) don't, and nothing runs off the foot (#688)."""
    long = "Open the mobiGlas and pick the vehicle maintenance services app"
    short = tuple(Row(f"Step {i}", "A little more") for i in range(8))
    held = tuple(Row(f"{long} {i}", "A little more") for i in range(8))
    bare = tuple(Row(r.text) for r in held)
    assert _tile(Card("steps", rows=short)).height > _tile(Card("steps", rows=short[:4])).height
    assert _near(_tile(Card("steps", rows=short)), CORE.dim) > 0
    assert _tile(Card("steps", rows=held)).size == _tile(Card("steps", rows=bare)).size
    assert _tile(Card("steps", rows=held)).height <= MAX_CARD + 30


def test_a_steps_second_line_wraps_when_theres_room() -> None:
    more = "Free manufacturer loadout: longer wait. Paid insured loadout: pay for the parts."
    one = Card("steps", rows=(Row("Choose a loadout", more),))
    cut = Card("steps", rows=(Row("Choose a loadout", more[:30]),))
    assert _tile(one).height > _tile(cut).height


def test_a_long_title_wraps_inside_the_card() -> None:
    """Its letters are spaced, which the wrapping didn't count: a title ran off the edge."""
    title = "STARLIGHT SERVICE STATION TO LEVSKI"
    f = font("label", 50)
    lines = wrap(title, f, 728, 2, 0.05 * f.size)
    assert len(lines) == 2
    assert all(text_width(line, f, 0.05 * f.size) <= 728 for line in lines)
    assert len(wrap(title, f, 728, 2)) == 1  # without the spacing it "fits", and ran over


def test_a_step_shows_its_one_line_more() -> None:
    bare = Card("steps", title="Claim", rows=(Row("Find an ASOP terminal"),))
    told = Card("steps", title="Claim", rows=(Row("Find an ASOP terminal", "In any spaceport"),))
    assert _tile(told).height > _tile(bare).height


class _Sized:
    """As much of a pen as ``snug`` needs: fonts, at twice the size."""

    def font(self, face: Any, size: int) -> Any:
        return font(face, size)

    def px(self, n: float) -> int:
        return round(n * 2)


def test_a_figure_too_wide_is_drawn_smaller_not_cut() -> None:
    pen: Any = _Sized()
    text, f = snug(pen, "100,000 / 91,000", "number", 20, 300)
    assert text == "100,000 / 91,000"
    assert f.size < 40
    assert snug(pen, "72 SCU", "number", 20, 300)[1].size == 40
    assert snug(pen, "x" * 80, "number", 20, 300)[0].endswith("…")


def test_a_spec_with_a_figure_and_more_shows_both() -> None:
    both = Card("ship", rows=(Row("Crew / cargo", "4 crew", "72 SCU"), Row("Role", "Fighter")))
    bare = Card("ship", rows=(Row("Crew / cargo", "", "72 SCU"), Row("Role", "Fighter")))
    assert _tile(both).height > _tile(bare).height
    assert _near(_tile(both), CORE.dim) > _near(_tile(bare), CORE.dim)


def test_a_recipe_shows_where_each_thing_is_from() -> None:
    told = Card("recipe", rows=(Row("Iron", "CRU L1, ship mining", "0.02 SCU"),))
    bare = Card("recipe", rows=(Row("Iron", "", "0.02 SCU"),))
    assert _tile(told).height > _tile(bare).height


def test_a_list_whose_values_are_all_the_same_lights_no_best_row() -> None:
    look = Look(accent="#ff00ff")
    same = Card("list", rows=(Row("Vuur", value="3.6%"), Row("Adir", value="3.6%")))
    lit = Card("list", rows=(Row("Vuur", value="3.6%"), Row("Adir", value="3.5%")))
    assert _near(_tile(same, look), "#ff00ff") < _near(_tile(lit, look), "#ff00ff")


def test_a_fleets_wordy_verdict_gives_way_to_the_ships_name() -> None:
    """The name's side of the card is the same however long the verdict: never cut."""
    long = Row("Constellation Taurus", "why", "Dogfighting and escort work, mostly")
    short = Row("Constellation Taurus", "why", "Best")
    wordy, brief = (_tile(Card("fleet", rows=(r,))) for r in (long, short))
    side = (0, 0, 190, brief.height)
    assert wordy.size == brief.size
    assert wordy.crop(side).tobytes() == brief.crop(side).tobytes()


def test_a_title_that_repeats_whats_by_it_is_left_off() -> None:
    answer = long_answer("Quantum fuel runs down as you jump.")
    named = long_answer("Quantum fuel runs down as you jump.", "Quantum fuel")
    assert answer.title_repeats
    assert not named.title_repeats
    assert _tile(named).height > _tile(answer).height
    sides = Card("compare", "M80 vs Arrow", hero=("M80", "Arrow"), rows=(Row("Hull", "1", "2"),))
    assert sides.title_repeats
    assert not Card("compare", "Fighters", hero=("M80", "Arrow")).title_repeats
    assert not Card("list", "Laranite").title_repeats


def test_a_question_says_that_waiting_presses_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    card = confirm("Self destruct the ship right now? Say confirm, Commander Ace.", 5)
    told = _near(_tile(card, now=0), CORE.dim)
    monkeypatch.setattr("tycho.tile.draw.UNANSWERED", "")
    assert UNANSWERED == "Wait, and nothing is pressed."
    assert told > _near(_tile(replace(card, seconds=6), now=0), CORE.dim)


def test_a_list_with_no_values_or_a_shared_unit_still_knows_its_best_row() -> None:
    look = Look(accent="#ff00ff")

    def lit(*values: str) -> int:
        rows = tuple(Row(f"Place {i}", value=v) for i, v in enumerate(values))
        return _near(_tile(Card("list", rows=rows), look), "#ff00ff", 90)

    assert lit("", "") > lit("3.6%", "3.6%")  # best first, with nothing to compare
    assert lit("8,500 aUEC/SCU", "8,400 aUEC/SCU") > lit("8,500 aUEC/SCU", "8,500 aUEC/SCU")


def test_a_comparisons_title_stays_when_it_says_more_than_the_names() -> None:
    hero = ("M80", "Arrow")
    assert Card("compare", "M80 vs Arrow", hero=hero).title_repeats
    assert Card("compare", "M80 versus Arrow.", hero=hero).title_repeats
    assert not Card("compare", "M80 vs Arrow for racing", hero=hero).title_repeats
    assert not Card("compare", "Cargo, M80 vs Arrow", hero=hero).title_repeats


def test_a_fleet_is_numbered_and_lit_only_when_it_says_what_its_ranked_for() -> None:
    """ "What are my ships good for" isn't a ranking: no 1 to 5, no best row."""
    look = Look(accent="#ff00ff")
    rows = (Row("Taurus", "174 SCU", "Best"), Row("Galaxy", "64 SCU", "Good"))
    ranked = _tile(Card("fleet", rows=rows, hero=("Best for", "Hauling")), look)
    plain = _tile(Card("fleet", rows=rows), look)
    assert ranked.size == plain.size
    assert _near(ranked, "#ff00ff", 90) > _near(plain, "#ff00ff", 90) + 100


def test_a_list_draws_a_shared_unit_once() -> None:
    """#688 (13): as drawn, not only what ``shared_unit`` returns."""

    def ink(unit_b: str) -> int:
        rows = (
            Row("Everus Harbor", "Hurston", "8,500 aUEC/SCU"),
            Row("Area18 TDD", "ArcCorp", f"7,700 {unit_b}"),
        )
        return _near(_tile(Card("list", rows=rows)), CORE.text) + _near(
            _tile(Card("list", rows=rows)), CORE.dim
        )

    assert ink("aUEC/SCU") < ink("aUEC/SCX")  # one unit said once, not one per row


def test_a_comparison_says_a_unit_both_figures_share_once() -> None:
    """#688 (1): "6,840,000 aUEC" and "24,250,000 aUEC" crowded the middle."""

    def card(right: str) -> Card:
        rows = (Row("Buy in game", "6,840,000 aUEC", right),)
        return Card("compare", hero=("Hammerhead", "Idris"), rows=rows)

    shared, apart = _tile(card("24,250,000 aUEC")), _tile(card("24,250,000 aUEX"))
    assert shared.size == apart.size
    assert _near(shared, CORE.text) < _near(apart, CORE.text)


def test_a_routes_long_place_names_wrap_rather_than_lose_their_end() -> None:
    """#688 (4): "Starlight Service S…" though the title had it in full."""

    def route(buy: str, sell: str) -> Card:
        return Card(
            "route", title="Agricium", rows=(Row(buy, "Buy", "2,600"), Row(sell, "Sell", "2,900"))
        )

    short = _tile(route("Lorville", "Area18"))
    long = _tile(route("Starlight Service Station", "Rayari Deltana Research Outpost"))
    assert long.height > short.height
    assert _tile(route("", "")).height == _tile(route("A", "B")).height


def test_one_line_rows_give_their_bars_room_under_the_name() -> None:
    """#688 (6): the bar sat on the name's baseline and read as an underline."""
    spread = (Row("A", value="3,048"), Row("B", value="1,500"), Row("C", value="900"))
    close = (Row("A", value="3,048"), Row("B", value="3,012"), Row("C", value="2,990"))
    assert _tile(Card("list", rows=spread)).height > _tile(Card("list", rows=close)).height


def test_spaced_letters_are_measured_as_caps_draws_them() -> None:
    """#688 (11): one letter at a time, so with no kerning between them."""
    f = font("label", 25)
    text = "AVATAR WAVY TYPE"
    tracking = 0.05 * f.size
    drawn = sum(f.getlength(ch) for ch in text) + tracking * (len(text) - 1)
    assert text_width(text, f, tracking) == pytest.approx(drawn)
    assert text_width(text, f) == f.getlength(text)
