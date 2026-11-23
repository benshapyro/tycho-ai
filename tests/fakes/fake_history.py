"""Sessions kept in memory, for history tests (satisfies ``history.HistoryStore``)."""

from tycho.history import Entry, Played, started_at


class FakeHistory:
    def __init__(self, sessions: dict[str, list[Entry]] | None = None) -> None:
        self.kept: dict[str, list[Entry]] = {k: list(v) for k, v in (sessions or {}).items()}

    def sessions(self) -> list[Played]:
        found = [Played(k, at, len(v)) for k, v in self.kept.items() if (at := started_at(k))]
        return sorted(found, key=lambda p: p.started, reverse=True)

    def entries(self, session: str) -> list[Entry]:
        return list(self.kept.get(session, []))

    def append(self, session: str, entry: Entry) -> None:
        self.kept.setdefault(session, []).append(entry)

    def delete(self, session: str) -> bool:
        return self.kept.pop(session, None) is not None
