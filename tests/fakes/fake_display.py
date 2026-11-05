"""A Tile that records what it was told. Satisfies ``runtime.Display``."""

from tycho.tile import Board, Card


class FakeDisplay:
    def __init__(self, *, on: bool = True) -> None:
        self.on = on
        self.board = Board(clock=lambda: 0.0)
        self.calls: list[str] = []
        self.shown: list[Card] = []
        self.light: str | None = None

    def listening(self) -> None:
        self.light = "listening"
        self.calls.append("listening")

    def thinking(self) -> None:
        self.light = "thinking"
        self.calls.append("thinking")

    def idle(self) -> None:
        self.light = None
        self.calls.append("idle")

    def show(self, card: Card, *, pinned: bool = False) -> bool:
        self.calls.append(f"show {card.kind}{' pinned' if pinned else ''}")
        if not self.on:
            return False
        self.shown.append(card)
        return self.board.show(card, pinned=pinned)

    def ask(self, question: str, seconds: float) -> None:
        self.calls.append(f"ask {question} ({seconds:g} s)")

    def answered(self) -> None:
        self.calls.append("answered")

    def pin(self) -> bool:
        self.calls.append("pin")
        return self.board.pin()

    def again(self) -> bool:
        self.calls.append("again")
        return self.board.again()

    def clear(self) -> None:
        self.calls.append("clear")
        self.board.clear()
