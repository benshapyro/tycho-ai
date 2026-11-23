"""What the Tile shows as a request ends, and the Tile's own words: keep, clear or
bring back a card, or show the last answer to read (ADR 16). Out of ``session.py``
(#399)."""

from tycho.runtime.messages import Progress, Result, Status, Trace
from tycho.runtime.ports import Display
from tycho.tile import Card
from tycho.tile.cards import long_answer


def tile_word(display: Display, what: str, last_reply: tuple[str, Card | None]) -> str:
    """Do what the player said to the Tile: "" once done, or why it couldn't be."""
    if what == "pin":
        done = display.pin()
    elif what == "again":
        # With no card that gave way, the last answer's card comes back (#565).
        done = display.again() or show_last(display, last_reply, card_only=True)
    elif what == "forward":
        done = display.forward()
    elif what == "clear":
        display.clear()
        done = True
    else:
        done = show_last(display, last_reply)
    if done:
        return ""
    return "nothing on the Tile for that" if what != "show" else "nothing to show"


def show_last(
    display: Display, last_reply: tuple[str, Card | None], *, card_only: bool = False
) -> bool:
    """Put the last answer up and keep it there: its card, or (unless ``card_only``)
    its words to read. False if there's nothing to show."""
    said, card = last_reply
    if card is not None:
        return display.show(card, pinned=True)
    return not card_only and bool(said) and display.show(long_answer(said))


def tile_request(
    display: Display,
    what: str,
    last_reply: tuple[str, Card | None],
    request: int,
    trace: Trace,
    heard: str,
    text: str,
) -> Result:
    """A request that was a word to the Tile, done (``tile_word``)."""
    detail = tile_word(display, what, last_reply)
    status: Status = "not_understood" if detail else "done"
    progress = Progress(commands=(f"tile_{what}",))
    return progress.result(request, status, trace, heard, text, detail)


def to_tile(display: Display, result: Result, *, listening: bool, confirm_seconds: float) -> None:
    """The talk light goes out, and a reply's card or the gate's question goes up."""
    if not listening:  # a barge-in: the light is already the new request's
        display.idle()
    if result.status == "replied":
        if result.card is not None:
            display.show(result.card)
    elif result.status == "asking" and result.question:
        display.ask(result.question, confirm_seconds)
