"""The History page (ADR 14's amendment, #484): each play session kept, what was said
in it, a search box, and deleting one session or all of them. Read fresh from the
files each time; what's over 30 days old is deleted first."""

import logging

from tycho import paths, settings
from tycho.desktop.bridge.state import State
from tycho.history import KEEP_DAYS, Entry, History, kept

log = logging.getLogger(__name__)

SEARCH_MOST = 40
"""The most exchanges a search on the page lists."""


class HistoryPage(State):
    def history(self) -> dict[str, object]:
        """Whether history is on, and every session kept, newest first."""
        on = settings.load(self._path).keep_history
        try:
            sessions = _history().store.sessions()
        except OSError as e:
            return {"on": on, "days": KEEP_DAYS, "sessions": [], "problem": str(e)}
        return {
            "on": on,  # None: never asked, and off
            "days": KEEP_DAYS,
            "folder": str(paths.history_dir()),
            "sessions": [
                {"id": s.id, "started": s.started.isoformat(), "exchanges": s.exchanges}
                for s in sessions
            ],
        }

    def history_session(self, session: object) -> dict[str, object]:
        """What was said in one session, in order."""
        if not isinstance(session, str):
            return {"ok": False, "problem": "That isn't a session Tycho keeps."}
        return {"ok": True, "entries": [_shown(e) for e in _history().store.entries(session)]}

    def search_history(self, words: object) -> dict[str, object]:
        """Every kept exchange that matches ``words``, the best ``SEARCH_MOST``."""
        if not isinstance(words, str) or not words.strip():
            return {"ok": False, "problem": "Type a word or two to look for."}
        found = _history().search(words[:100], most=SEARCH_MOST)
        return {"ok": True, "found": [{"session": f.session, **_shown(f.entry)} for f in found]}

    def delete_history(self, session: object) -> dict[str, object]:
        """Delete one session, by its id, or every one with ``"all"``."""
        history = _history()
        try:
            if session == "all":
                gone = history.delete_all()
                log.info("History: every session deleted on the History page (%d)", gone)
                return {"ok": True, "deleted": gone}
            if not isinstance(session, str) or not history.delete(session):
                return {"ok": False, "problem": "That session was already gone."}
        except OSError as e:
            return {"ok": False, "problem": f"Couldn't delete it: {e}"}
        log.info("History: a session deleted on the History page")
        return {"ok": True, "deleted": 1}


def _history() -> History:
    """The kept sessions, whether history is on or not: off keeps them, and the page
    still shows and deletes them."""
    return kept(on=False)


def _shown(entry: Entry) -> dict[str, object]:
    return {
        "at": entry.at.isoformat(),
        "said": entry.said,
        "tycho": entry.tycho,
        "did": list(entry.did),
    }
