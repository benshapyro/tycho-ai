"""Notes kept in memory, for memory tests (satisfies ``memory.NoteStore``)."""

import datetime

from tycho.memory import Kept


class FakeNotes:
    def __init__(self, *notes: str, problem: str = "") -> None:
        self.listed = [Kept(n) for n in notes]
        self.problem = problem

    def notes(self) -> list[Kept]:
        return list(self.listed)

    def add(self, note: str, saved: datetime.date) -> None:
        self.listed.append(Kept(note, saved))

    def remove(self, note: str) -> bool:
        for kept in self.listed:
            if kept.text == note:
                self.listed.remove(kept)
                return True
        return False
