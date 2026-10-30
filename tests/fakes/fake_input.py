from tycho.ptt.binding import Binding


class FakeInputSource:
    """An input source whose held buttons are set by the test."""

    def __init__(self, *held: Binding) -> None:
        self.held: set[Binding] = set(held)

    def pressed(self) -> frozenset[Binding]:
        return frozenset(self.held)

    def is_pressed(self, binding: Binding) -> bool:
        return binding in self.held
