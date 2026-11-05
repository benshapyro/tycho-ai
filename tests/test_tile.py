"""The Tile (#2): the card kit Claude fills, and the rules for what shows and for how long."""

from typing import Any

import pytest

from tycho.tile import BRAIN_KINDS, Board, Card, Row, from_tool, lifetime
from tycho.tile.cards import ALL_KINDS, TITLE, TOOL_SCHEMA, clean, confirm, long_answer
from tycho.tile.design import TYCHO_ACCENT, accent_for


def _call(kind: str = "list", **fields: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "say": "Best is Everus Harbor.",
        "kind": kind,
        "label": "Sell · Laranite",
        "title": "Best places to sell",
        "source": "UEX",
        "rows": [
            {"text": "Everus Harbor", "detail": "Hurston", "value": "3,048", "dim": False},
            {"text": "Area18 TDD", "detail": "ArcCorp", "value": "3,012", "dim": False},
        ],
        "hero_label": "",
        "hero_value": "",
        "text": "",
    }
    return call | fields


# --- the card kit ----------------------------------------------------------------


def test_a_list_card_from_claudes_call() -> None:
    card = from_tool(_call())
    assert card.kind == "list"
    assert (card.title, card.label, card.source) == (
        "Best places to sell",
        "Sell · Laranite",
        "UEX",
    )
    assert card.rows[0] == Row("Everus Harbor", "Hurston", "3,048")
    assert card.hero is None
    assert card.body == ()


def test_a_route_has_a_buy_a_sell_and_the_profit() -> None:
    card = from_tool(_call("route", hero_label="Profit, full load", hero_value="61,728"))
    assert card.kind == "route"
    assert card.hero == ("Profit, full load", "61,728")


def test_a_route_that_isnt_a_buy_and_a_sell_is_shown_as_a_list() -> None:
    rows = [{"text": t, "detail": "", "value": "1", "dim": False} for t in "ABC"]
    assert from_tool(_call("route", rows=rows)).kind == "list"


def test_a_warning_carries_the_exact_words() -> None:
    card = from_tool(_call("warning", rows=[], text="Keybindings, search lock all doors."))
    assert card.body == ("Keybindings, search lock all doors.",)
    assert card.tone == "caution"


@pytest.mark.parametrize("kind", ["confirm", "long", "notice", "html", ""])
def test_claude_cant_show_tychos_own_cards_or_invent_one(kind: str) -> None:
    with pytest.raises(ValueError, match="isn't a card"):
        from_tool(_call(kind))


def test_an_empty_card_is_refused() -> None:
    with pytest.raises(ValueError, match="nothing on it"):
        from_tool(_call(rows=[{"text": "  ", "detail": "", "value": "", "dim": False}]))


def test_what_claude_writes_is_plain_text_cut_to_fit() -> None:
    card = from_tool(_call(title="**Best** <b>places</b>\nto `sell` " + "x" * 80))
    assert "<" not in card.title
    assert "*" not in card.title
    assert "\n" not in card.title
    assert len(card.title) == TITLE
    assert card.title.endswith("…")


def test_control_and_direction_characters_are_removed() -> None:
    assert clean("Ever\u202eus\x00 Harbor\u200b", 40) == "Ever us Harbor"
    assert clean(None, 40) == ""


def test_too_many_rows_are_cut_and_odd_rows_skipped() -> None:
    rows: list[object] = [
        {"text": f"Row {i}", "detail": "", "value": "", "dim": False} for i in range(9)
    ]
    rows.insert(0, "not a row")
    assert len(from_tool(_call(rows=rows)).rows) == 6
    assert len(from_tool(_call("steps", rows=rows)).rows) == 8


def test_a_dim_row_must_say_so() -> None:
    rows = [{"text": "Cutlass", "detail": "", "value": "", "dim": "yes"}]
    assert from_tool(_call("fleet", rows=rows)).rows[0].dim is False


def test_the_tool_schema_suits_strict_tool_use() -> None:
    assert TOOL_SCHEMA["additionalProperties"] is False
    properties: dict[str, Any] = dict(TOOL_SCHEMA["properties"])  # type: ignore[arg-type]
    assert sorted(TOOL_SCHEMA["required"]) == sorted(properties)  # type: ignore[arg-type]
    assert properties["kind"]["enum"] == list(BRAIN_KINDS)
    row = properties["rows"]["items"]
    assert row["additionalProperties"] is False
    assert sorted(row["required"]) == sorted(row["properties"])


def test_tychos_own_cards() -> None:
    question = confirm("Eject? Say confirm, Ace.", 5.0)
    assert (question.kind, question.tone, question.seconds) == ("confirm", "danger", 5.0)
    assert long_answer("Quantum fuel is a tank of its own.").body == (
        "Quantum fuel is a tank of its own.",
    )
    assert set(ALL_KINDS) == {*BRAIN_KINDS, "long", "confirm", "notice"}


# --- the board -------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _card(title: str = "A", kind: Any = "list") -> Card:
    return Card(kind, title=title, rows=(Row("Everus Harbor", value="3,048"),))


def test_longer_cards_stay_longer_within_limits() -> None:
    short, long = _card(), Card("list", rows=tuple(Row("x" * 60, "y" * 70) for _ in range(6)))
    assert 6.0 <= lifetime(short) < lifetime(long) == 20.0
    assert lifetime(short, stay=1.6) == pytest.approx(lifetime(short) * 1.6)


def test_a_card_fades_by_itself() -> None:
    clock = Clock()
    board = Board(clock=clock)
    assert board.show(_card())
    assert len(board.view()) == 1
    clock.now += lifetime(_card()) + 0.1
    assert board.view() == ()


def test_one_at_a_time_a_new_card_replaces_the_last() -> None:
    board = Board(clock=Clock())
    board.show(_card("A"))
    board.show(_card("B"))
    assert [s.card.title for s in board.view()] == ["B"]


def test_a_pinned_card_shrinks_to_a_strip_and_comes_back() -> None:
    clock = Clock()
    board = Board(clock=clock)
    board.show(_card("A"))
    assert board.pin()
    board.show(_card("B"))
    view = board.view()
    assert [(s.card.title, s.strip) for s in view] == [("B", False), ("A", True)]
    clock.now += 60  # B fades; the strip stays
    assert [(s.card.title, s.strip) for s in board.view()] == [("A", True)]
    assert board.again()
    assert [(s.card.title, s.strip, s.pinned) for s in board.view()] == [("A", False, True)]


def test_only_one_strip_is_kept() -> None:
    board = Board(clock=Clock())
    for title in "ABC":
        board.show(_card(title), pinned=True)
    assert [(s.card.title, s.strip) for s in board.view()] == [("C", False), ("B", True)]


def test_two_at_once_the_oldest_gives_way() -> None:
    board = Board(clock=Clock(), at_once=2)
    for title in "ABC":
        board.show(_card(title))
    assert [s.card.title for s in board.view()] == ["C", "B"]


def test_nothing_to_pin_or_bring_back() -> None:
    board = Board(clock=Clock())
    assert not board.pin()
    assert not board.again()


def test_a_long_answer_is_pinned_since_the_player_asked_to_read_it() -> None:
    board = Board(clock=Clock())
    board.show(long_answer("Quantum fuel is a tank of its own."))
    assert board.view()[0].pinned


def test_a_confirm_card_lasts_as_long_as_its_question() -> None:
    clock = Clock()
    board = Board(clock=clock)
    board.show(confirm("Eject? Say confirm.", 5.0))
    clock.now += 4.9
    assert board.view()
    clock.now += 0.2
    assert board.view() == ()


def test_an_answered_question_takes_its_card_down() -> None:
    board = Board(clock=Clock())
    board.show(confirm("Eject? Say confirm.", 5.0))
    board.drop("confirm")
    assert board.view() == ()


def test_a_hidden_kind_isnt_shown_but_confirm_always_is() -> None:
    board = Board(clock=Clock(), hidden={"headlines", "confirm"})
    assert not board.show(_card(kind="headlines"))
    assert board.show(confirm("Eject? Say confirm.", 5.0))


def test_quiet_mode_shows_only_confirm_warnings_and_notices() -> None:
    board = Board(clock=Clock(), quiet=True)
    assert not board.show(_card())
    assert board.show(Card("warning", body=("No key.",)))
    assert board.allows("notice")
    assert board.allows("confirm")
    assert not board.allows("ship")


def test_clear_empties_the_tile() -> None:
    board = Board(clock=Clock())
    board.show(_card(), pinned=True)
    board.clear()
    assert board.view() == ()


# --- the design system -----------------------------------------------------------


@pytest.mark.parametrize(
    ("ship", "accent"),
    [
        ("DRAK_Cutlass_Black", "#f5901e"),
        ("misc_prospector", "#58ddd1"),
        ("RSI_Aurora_MR", "#4fb3ff"),
    ],
)
def test_the_accent_follows_the_ships_maker(ship: str, accent: str) -> None:
    assert accent_for(ship) == accent


@pytest.mark.parametrize("ship", [None, "", "Unknown_Ship", "nounderscore"])
def test_no_known_maker_is_tychos_own_accent(ship: str | None) -> None:
    assert accent_for(ship) == TYCHO_ACCENT
