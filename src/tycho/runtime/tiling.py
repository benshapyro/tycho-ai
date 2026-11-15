"""What the Tile shows as a request ends, and the Tile's own words: keep, clear or
bring back a card, or show the last answer to read (ADR 16). Out of ``session.py``
(#399)."""

from tycho.runtime.messages import Result
from tycho.runtime.ports import Display
from tycho.tile import Card
from tycho.tile.cards import long_answer


def tile_word(display: Display, what: str, last_reply: tuple[str, Card | None]) -> str:
    """Do what the player said to the Tile: "" once done, or why it couldn't be."""
    if what == "pin":
        done = display.pin()
    elif what == "again":
        done = display.again()
    elif what == "clear":
        display.clear()
        done = True
    else:
        said, card = last_reply
        if card is not None:
            done = display.show(card, pinned=True)
        else:
            done = bool(said) and display.show(long_answer(said))
    if done:
        return ""
    return "nothing on the Tile for that" if what != "show" else "nothing to show"


def to_tile(display: Display, result: Result, *, listening: bool, confirm_seconds: float) -> None:
    """The talk light goes out, and a reply's card or the gate's question goes up."""
    if not listening:  # a barge-in: the light is already the new request's
        display.idle()
    if result.status == "replied":
        if result.card is not None:
            display.show(result.card)
    elif result.status == "asking" and result.question:
        display.ask(result.question, confirm_seconds)
