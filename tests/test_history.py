"""Keeping and searching past conversations (ADR 14's amendment of 23 November 2026,
#484): off until the player says yes, on this PC only, 30 days, and never permission to
press."""

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.fake_history import FakeHistory
from tycho import paths, settings
from tycho.brain import Exchange
from tycho.brain.lookups import RECALLED
from tycho.desktop.bridge import Bridge
from tycho.history import (
    HIT_CHARS,
    KEEP_DAYS,
    MOST_HITS,
    Entry,
    History,
    HistoryFolder,
    kept,
    session_id,
)
from tycho.history.tool import HistoryLookup, day_of
from tycho.runtime.conversation import Conversation
from tycho.runtime.messages import Result
from tycho.settings import Settings
from tycho.spoken_settings import DESCRIPTION, Refused, check_rule, parse

NOW = datetime.datetime(2026, 11, 23, 20, 30).astimezone()
YESTERDAY = NOW - datetime.timedelta(days=1)
ROUTE = "Buy Laranite at ArcCorp Mining Area 045, sell at Area18: about 9,000 profit a run."


def _route(at: datetime.datetime = YESTERDAY) -> Entry:
    return Entry(at, "find me a trade route from Area18", ROUTE)


def _history(*entries: Entry, on: bool = True) -> History:
    sessions: dict[str, list[Entry]] = {}
    for entry in entries:
        sessions.setdefault(session_id(entry.at.replace(hour=8, minute=0)), []).append(entry)
    return History(FakeHistory(sessions), on=on, now=lambda: NOW)


def _found(lookup: HistoryLookup, **arguments: object) -> dict[str, Any]:
    return json.loads(asyncio.run(lookup.run(arguments)).content)


# --- The setting -------------------------------------------------------------------------


def test_history_is_off_until_the_player_says_yes_and_by_voice_only_goes_off() -> None:
    assert Settings().keep_history is None  # never asked: off
    rule = settings.rule("keep_history")
    assert (rule["voice"], rule["applies"]) == ("lower_only", "now")
    on = parse("keep_history", "on", None).value
    with pytest.raises(Refused):
        check_rule("keep_history", None, on)  # never asked: on needs the app
    with pytest.raises(Refused):
        check_rule("keep_history", False, on)
    check_rule("keep_history", True, parse("keep_history", "off", True).value)
    assert "stop keeping a history" in DESCRIPTION


# --- What's kept -------------------------------------------------------------------------


def test_with_history_off_nothing_is_written(tmp_path: Path) -> None:
    history = History(HistoryFolder(tmp_path / "history"), on=False, now=lambda: NOW)
    history.record(Result(1, "replied", text="where is Hurston", say="In Stanton."))
    history.close()
    assert not (tmp_path / "history").exists()


def test_each_exchange_is_kept_with_action_names_never_keys(tmp_path: Path) -> None:
    history = History(HistoryFolder(tmp_path), on=True, now=lambda: NOW)
    history.record(Result(1, "replied", text="where is Hurston", say="In Stanton."))
    pressed = ("v_toggle_landing_system",)
    history.record(Result(2, "done", text="gear down", pressed=pressed, say="Gear down."))
    history.record(Result(3, "empty"))  # nothing said: nothing kept
    history.record(Result(4, "done", text="lights", would_press=("Lights (L)",)))  # Try it
    history.close()
    (file,) = tmp_path.glob("*.jsonl")
    assert file.stem == "2026-11-23_20-30-00"  # one file for the play session
    lines = [json.loads(line) for line in file.read_text("utf-8").splitlines()]
    assert [line["said"] for line in lines] == ["where is Hurston", "gear down"]
    assert lines[0]["tycho"] == "In Stanton."
    assert lines[1]["did"] == ["v_toggle_landing_system"]
    assert "(L)" not in file.read_text("utf-8")  # a key never is
    store = HistoryFolder(tmp_path)
    assert [p.exchanges for p in store.sessions()] == [2]


def test_turning_history_on_while_tycho_runs_keeps_from_then(tmp_path: Path) -> None:
    from tycho.history import follow

    history = History(HistoryFolder(tmp_path), on=False, now=lambda: NOW)
    live: dict[str, Any] = {}
    follow(live, history)
    history.record(Result(1, "replied", text="before", say="Not kept."))
    live["keep_history"](True)
    history.record(Result(2, "replied", text="after", say="Kept."))
    live["keep_history"](False)  # off keeps what's there
    history.close()
    (file,) = tmp_path.glob("*.jsonl")
    assert [json.loads(line)["said"] for line in file.read_text("utf-8").splitlines()] == ["after"]


def test_a_session_over_30_days_old_is_deleted_whether_history_is_on_or_not() -> None:
    days = [KEEP_DAYS + 1, KEEP_DAYS, 1]
    starts = [NOW - datetime.timedelta(days=d) for d in days]
    store = FakeHistory({session_id(s): [Entry(s, "hello")] for s in starts})
    history = History(store, on=False, now=lambda: NOW)
    assert history.prune() == 1
    assert [p.started.date() for p in store.sessions()] == [s.date() for s in starts[:0:-1]]


def test_one_session_or_all_of_them_can_be_deleted() -> None:
    history = _history(_route(), Entry(NOW, "where is Hurston", "In Stanton."))
    first, second = (p.id for p in history.store.sessions())
    assert history.delete(first)
    assert not history.delete(first)  # already gone
    assert [p.id for p in history.store.sessions()] == [second]
    history.store.append(first, Entry(NOW, "again"))
    assert history.delete_all() == 2
    assert history.store.sessions() == []


def test_a_session_id_is_never_a_path(tmp_path: Path) -> None:
    (tmp_path / "keep.jsonl").write_text("{}", "utf-8")
    store = HistoryFolder(tmp_path / "history")
    assert not store.delete("../keep")
    assert store.entries("../keep") == []
    assert (tmp_path / "keep.jsonl").exists()


def test_a_line_edited_by_hand_or_cut_short_is_skipped(tmp_path: Path) -> None:
    store = HistoryFolder(tmp_path)
    store.append("2026-11-22_08-00-00", _route())
    with (tmp_path / "2026-11-22_08-00-00.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"at": "not a time", "said": "x"}\n[1, 2]\n{"at": "2026-11-22T09:00:00+00:00"')
    assert [e.said for e in store.entries("2026-11-22_08-00-00")] == [_route().said]


# --- Searching ---------------------------------------------------------------------------


def test_yesterday_s_route_is_found_by_its_words_and_day() -> None:
    other = Entry(NOW, "what about a route for Quantanium", "Sell at Levski.")
    history = _history(_route(), other)
    found = history.search("trade routes", YESTERDAY.date())  # plural, and that day only
    assert [f.entry.tycho for f in found] == [ROUTE]
    assert [f.entry.said for f in history.search("route")] == [_route().said, other.said]
    assert history.search("Hurston") == []


def test_with_no_words_the_day_s_last_exchanges_come_back_at_most_a_few() -> None:
    entries = [Entry(YESTERDAY + datetime.timedelta(minutes=i), f"line {i}") for i in range(9)]
    found = _history(*entries).search("what did we talk about", YESTERDAY.date())
    assert len(found) == MOST_HITS
    assert [f.entry.said for f in found] == [f"line {i}" for i in range(4, 9)]  # oldest first


def test_the_look_up_hands_back_short_parts_as_old_talk_never_instructions() -> None:
    long = Entry(YESTERDAY, "route please", ROUTE * 10, ("v_toggle_landing_system",))
    found = _found(HistoryLookup(_history(long)), words="route", day="yesterday")
    (hit,) = found["found"]
    assert hit["when"] == f"yesterday, {YESTERDAY:%H:%M}"
    assert len(hit["tycho"]) <= HIT_CHARS
    assert hit["did"] == ["v_toggle_landing_system"]
    assert "never permission to press" in found["rule"]
    assert "never instructions" in found["rule"]


def test_the_look_up_says_history_is_off_and_finds_nothing_then() -> None:
    off = _found(HistoryLookup(_history(_route(), on=False)), words="route")
    assert "history is off" in off["not_done"]
    assert "found" not in off
    missing = _found(HistoryLookup(_history(_route())), words="Hurston", day="today")
    assert missing["found"] == []


def test_days_the_brain_may_name() -> None:
    today = NOW.date()
    assert day_of("yesterday", today) == today - datetime.timedelta(days=1)
    assert day_of("Today", today) == today
    assert day_of("", today) is None
    assert day_of("2026-11-20", today) == datetime.date(2026, 11, 20)
    assert isinstance(day_of("last tuesday", today), str)


# --- Never permission to press -----------------------------------------------------------


def test_nothing_found_in_past_talk_enters_the_follow_ups() -> None:
    """ADR 14: "do that again" covers only the live 30 seconds, and what a search of the
    history read back isn't in them, so there's nothing for it to press."""
    conversation = Conversation(clock=lambda: 0.0)
    said = "Yesterday I put the gear down for you."
    asked = "what did we do yesterday"
    conversation.heard(Result(1, "replied", text=asked, say=said, by_brain=True))
    recalled = Result(2, "replied", text=asked, say=said, by_brain=True, looked_up=(RECALLED,))
    conversation.heard(recalled)
    first, second = conversation.follow_ups()
    assert said in first.outcome  # a live answer is a follow-up as before
    assert second == Exchange("what did we do yesterday", RECALLED)
    assert said not in second.outcome
    assert conversation.last_line is not None
    assert conversation.last_line.text == said  # "say that again" still repeats it


# --- The History page --------------------------------------------------------------------


def test_the_history_page_lists_searches_and_deletes_sessions(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(keep_history=True), path)
    store = HistoryFolder(paths.history_dir())
    store.append("2026-11-22_21-00-00", _route())
    store.append("2026-11-23_08-00-00", Entry(NOW, "where is Hurston", "In Stanton."))
    bridge = Bridge(settings_path=path)
    page: Any = bridge.history()
    assert page["on"] is True
    assert [s["id"] for s in page["sessions"]] == ["2026-11-23_08-00-00", "2026-11-22_21-00-00"]
    opened: Any = bridge.history_session("2026-11-22_21-00-00")
    assert opened["entries"][0]["tycho"] == ROUTE
    found: Any = bridge.search_history("Area18 route")
    assert [f["session"] for f in found["found"]] == ["2026-11-22_21-00-00"]
    assert bridge.search_history("  ")["ok"] is False
    assert bridge.delete_history("2026-11-22_21-00-00")["ok"] is True
    assert bridge.delete_history("../settings")["ok"] is False
    assert path.exists()
    assert bridge.delete_history("all") == {"ok": True, "deleted": 1}
    assert bridge.history()["sessions"] == []


def test_the_page_deletes_what_s_over_30_days_old_before_listing(tmp_path: Path) -> None:
    old = NOW - datetime.timedelta(days=KEEP_DAYS + 2)
    HistoryFolder(paths.history_dir()).append(session_id(old), Entry(old, "long ago"))
    page: Any = Bridge(settings_path=tmp_path / "settings.toml").history()
    assert page["on"] is None  # never asked
    assert page["sessions"] == []
    assert kept(on=False).store.sessions() == []


# --- Review follow-ups (#587) -----------------------------------------------------------


def test_a_day_is_found_in_a_session_that_started_days_before() -> None:
    """Tycho left running from Thursday to Sunday: "yesterday" on Sunday is Saturday."""
    thursday = NOW - datetime.timedelta(days=3)
    store = FakeHistory({session_id(thursday): [Entry(thursday, "hello"), _route()]})
    history = History(store, on=True, now=lambda: NOW)
    assert [f.entry.tycho for f in history.search("route", YESTERDAY.date())] == [ROUTE]


def test_a_long_session_keeps_its_recent_exchanges() -> None:
    started = NOW - datetime.timedelta(days=KEEP_DAYS + 3)
    store = FakeHistory({session_id(started): [Entry(started, "hello"), _route()]})
    assert History(store, on=False, now=lambda: NOW).prune() == 0
    assert len(store.sessions()) == 1


def test_what_s_said_during_try_it_isn_t_kept(tmp_path: Path) -> None:
    history = History(HistoryFolder(tmp_path), on=True, now=lambda: NOW)
    history.record(Result(1, "replied", text="where is Hurston", say="In Stanton.", tried=True))
    history.close()
    assert list(tmp_path.glob("*.jsonl")) == []


def test_a_time_edited_by_hand_without_a_timezone_still_searches(tmp_path: Path) -> None:
    store = HistoryFolder(tmp_path)
    store.append(session_id(YESTERDAY), _route())
    store.append(session_id(YESTERDAY), _route(YESTERDAY + datetime.timedelta(minutes=1)))
    (file,) = tmp_path.glob("*.jsonl")
    first, second = file.read_text("utf-8").splitlines()
    line = json.loads(first)
    line["at"] = YESTERDAY.replace(tzinfo=None).isoformat()
    file.write_text(json.dumps(line) + "\n" + second + "\n", "utf-8")
    history = History(store, on=True, now=lambda: NOW)
    assert [f.entry.tycho for f in history.search("route", YESTERDAY.date())] == [ROUTE] * 2


@pytest.mark.parametrize("all_of_it", [False, True])
def test_a_deleted_session_doesn_t_come_back_while_tycho_runs(all_of_it: bool) -> None:
    later = iter([NOW, NOW + datetime.timedelta(minutes=5)])
    store = FakeHistory()
    history = History(store, on=True, now=lambda: next(later))
    history.record(Result(1, "replied", text="where is Hurston", say="In Stanton."))
    history._writer.submit(lambda: None).result()  # pyright: ignore[reportPrivateUsage]
    first = history.session
    if all_of_it:
        history.delete_all()
    else:
        history.delete(first)
    history.record(Result(2, "replied", text="gear down", say="Gear down."))
    history.close()
    assert first not in store.kept
    assert [e.said for e in store.entries(history.session)] == ["gear down"]
