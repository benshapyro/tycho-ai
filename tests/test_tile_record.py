"""The Tile's record (a review tool): each card saved as drawn, with what it held."""

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from tycho import paths
from tycho.runtime.messages import Result
from tycho.runtime.tiling import to_tile
from tycho.settings import Settings
from tycho.tile import Card, Row
from tycho.tile import record as tile_record
from tycho.tile.board import Shown
from tycho.tile.cards import notice
from tycho.tile.display import TileDisplay
from tycho.tile.draw import Look
from tycho.tile.record import FILE, CardRecord
from tycho.tile.window import Frame, Screen

SCREEN = Screen(0, 0, 1920, 1080)
SELL = Card("list", title="Best places to sell", rows=(Row("Everus Harbor", value="3,048"),))
ROUTE = Card("route", title="Medical supplies", rows=(Row("Lorville", "Buy", "1,812"),))


class FakeWindow:
    def __init__(self, frame: Callable[[Screen], Frame]) -> None:
        self.frame = frame
        self.failed = ""

    def start(self) -> bool:
        return True

    def close(self) -> None:
        pass


class Ticks:
    """A wall clock that moves a second each time it's read."""

    def __init__(self) -> None:
        self.at = datetime(2026, 11, 24, 4, 30)

    def __call__(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at


def _tile(folder: Path, **settings: object) -> tuple[TileDisplay, CardRecord]:
    record = CardRecord(folder, now=Ticks(), background=False)
    display = TileDisplay(
        Settings(**settings),  # type: ignore[arg-type]
        clock=lambda: 100.0,
        window=FakeWindow,  # type: ignore[arg-type]
        record=record,
    )
    return display, record


def _lines(run: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run / FILE).read_text("utf-8").splitlines()]


def test_a_card_is_saved_as_drawn_with_what_was_asked(tmp_path: Path) -> None:
    display, record = _tile(tmp_path)
    to_tile(
        display,
        Result(1, "replied", heard="where can i sell laranite", say="Everus Harbor.", card=SELL),
        listening=False,
        confirm_seconds=8,
    )
    frame = display.frame(SCREEN)
    assert frame is not None
    assert record.run is not None
    [line] = _lines(record.run)
    assert line["kind"] == "list"
    assert line["asked"] == "where can i sell laranite"
    assert line["said"] == "Everus Harbor."
    card = line["card"]
    assert isinstance(card, dict)
    assert card["title"] == "Best places to sell"
    assert card["rows"] == [{"text": "Everus Harbor", "detail": "", "value": "3,048", "dim": False}]
    with Image.open(record.run / str(line["file"])) as picture:
        assert picture.size == frame[0].size  # type: ignore[index]
        assert line["size"] == list(picture.size)


def test_each_card_is_saved_once_and_the_picture_shows_the_strips(tmp_path: Path) -> None:
    display, record = _tile(tmp_path)
    display.show(SELL)
    display.frame(SCREEN)
    display.frame(SCREEN)  # drawn again: nothing new to save
    display.show(ROUTE)
    display.frame(SCREEN)
    assert record.run is not None
    lines = _lines(record.run)
    assert [line["kind"] for line in lines] == ["list", "route"]
    assert lines[1]["with"] == [{"kind": "list", "title": "Best places to sell", "strip": True}]
    assert sorted(p.name for p in record.run.glob("*.png")) == ["0001-list.png", "0002-route.png"]


def test_a_card_that_went_before_it_was_drawn_is_not_saved(tmp_path: Path) -> None:
    display, record = _tile(tmp_path)
    display.show(notice("Starting"))  # a notice just goes when the next card comes
    display.show(SELL)
    display.frame(SCREEN)
    assert record.run is not None
    assert [line["kind"] for line in _lines(record.run)] == ["list"]


def test_a_hidden_card_is_not_saved(tmp_path: Path) -> None:
    display, record = _tile(tmp_path, tile_hidden_cards="list")
    assert not display.show(SELL)
    display.frame(SCREEN)
    assert record.run is None


def test_only_the_newest_runs_are_kept(tmp_path: Path) -> None:
    ticks = Ticks()
    for _ in range(4):
        record = CardRecord(tmp_path, now=ticks, runs=3, background=False)
        record.shown(SELL)
        record.drawn(_view(record), Image.new("RGBA", (10, 10)), _look())
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 3


def test_a_run_stops_saving_at_its_limit(tmp_path: Path) -> None:
    display, record = _tile(tmp_path)
    record._limit = 2  # type: ignore[attr-defined]
    for card in (SELL, ROUTE, SELL):
        display.show(card)
        display.frame(SCREEN)
    assert record.run is not None
    assert len(_lines(record.run)) == 2


def test_saving_in_the_background_finishes_on_close(tmp_path: Path) -> None:
    record = CardRecord(tmp_path, now=Ticks())
    display = TileDisplay(Settings(), clock=lambda: 100.0, window=FakeWindow, record=record)  # type: ignore[arg-type]
    display.show(SELL)
    display.frame(SCREEN)
    display.close()
    assert record.run is not None
    assert len(_lines(record.run)) == 1


def test_off_unless_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "tile_cards_dir", lambda: tmp_path)
    monkeypatch.delenv(tile_record.ENV, raising=False)
    assert tile_record.from_env() is None
    monkeypatch.setenv(tile_record.ENV, "1")
    assert isinstance(tile_record.from_env(), CardRecord)


def _view(record: CardRecord) -> tuple[Shown, ...]:
    return tuple(Shown(entry[0], None) for entry in record._pending)  # type: ignore[attr-defined]


def _look() -> Look:
    return Look(accent="#f5901e", scale=1.0, see_through=80, high_contrast=False)


def test_the_sample_card_after_a_setting_changes_is_saved(tmp_path: Path) -> None:
    display, record = _tile(tmp_path)
    display.change("tile_see_through", 0.4)
    display.frame(SCREEN)
    assert record.run is not None
    [line] = _lines(record.run)
    assert line["kind"] == "notice"
    assert "see through" in str(line["said"])


def test_a_write_that_fails_is_dropped_and_the_tile_keeps_drawing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def full_disk(*_args: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(tile_record, "write", full_disk)
    record = CardRecord(tmp_path, now=Ticks())
    display = TileDisplay(Settings(), clock=lambda: 100.0, window=FakeWindow, record=record)  # type: ignore[arg-type]
    display.show(SELL)
    assert display.frame(SCREEN) is not None
    display.show(ROUTE)
    assert display.frame(SCREEN) is not None  # the writer's thread is still there
    display.close()
    assert record._thread is not None  # type: ignore[attr-defined]
    assert not record._thread.is_alive()  # type: ignore[attr-defined]


def test_a_full_queue_drops_a_card_without_waiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gate = threading.Event()
    written: list[str] = []

    def slow(path: Path, _image: Image.Image, _line: dict[str, object]) -> None:
        gate.wait(5)
        written.append(path.name)

    monkeypatch.setattr(tile_record, "write", slow)
    monkeypatch.setattr(tile_record, "WAITING", 2)
    record = CardRecord(tmp_path, now=Ticks())
    started = time.monotonic()
    for _ in range(6):  # one on the writer, two waiting, the rest dropped
        record.shown(SELL)
        record.drawn(_view(record), Image.new("RGBA", (10, 10)), _look())
    assert time.monotonic() - started < 1.0
    gate.set()
    record.close()
    assert 1 <= len(written) <= 3
    assert len(written) < 6


def test_closing_ends_the_writer_thread_and_nothing_is_saved_after(tmp_path: Path) -> None:
    record = CardRecord(tmp_path, now=Ticks())
    record.shown(SELL)
    record.drawn(_view(record), Image.new("RGBA", (10, 10)), _look())
    record.close()
    assert record._thread is not None  # type: ignore[attr-defined]
    assert not record._thread.is_alive()  # type: ignore[attr-defined]
    record.shown(ROUTE)
    record.drawn(_view(record), Image.new("RGBA", (10, 10)), _look())
    assert record.run is not None
    assert len(_lines(record.run)) == 1


def test_making_the_folder_and_pruning_happen_off_the_window_thread(tmp_path: Path) -> None:
    gate = threading.Event()
    record = CardRecord(tmp_path, now=Ticks())
    real = record._start  # type: ignore[attr-defined]
    threads: list[str] = []

    def start() -> Path:
        threads.append(threading.current_thread().name)
        gate.wait(5)
        return real()

    record._start = start  # type: ignore[method-assign]
    record.shown(SELL)
    record.drawn(_view(record), Image.new("RGBA", (10, 10)), _look())  # returns at once
    assert record.run is None
    gate.set()
    record.close()
    assert threads == ["tile-record"]
    assert record.run is not None
