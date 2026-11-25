"""The picture of the map when a route fails on it (``eyes.record``, TYCHO_ROUTE_RECORD)."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tycho import paths
from tycho.eyes import CaptureError, Picture
from tycho.eyes import record as route_record
from tycho.eyes.record import FILE, FrameRecord
from tycho.eyes.words import Reading, Word

READING = Reading(2560, 1440, (Word("STARMAP", 10, 10, 80, 20, 0),))


class Camera:
    def __init__(self, fails: bool = False) -> None:
        self.fails = fails
        self.taken = 0

    def take(self) -> Picture:
        self.taken += 1
        if self.fails:
            raise CaptureError("no StarCitizen.exe window is open")
        return Picture(b"jpeg", 1568, 882)


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 11, 25, 14, 0, 0)

    def __call__(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at


def _lines(folder: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (folder / FILE).read_text("utf-8").splitlines()]


def test_off_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(route_record.ENV, raising=False)
    assert route_record.from_env(Camera()) is None
    monkeypatch.setenv(route_record.ENV, "1")
    assert route_record.from_env(Camera()) is not None


def test_a_failure_saves_the_picture_and_what_was_read(tmp_path: Path) -> None:
    camera = Camera()
    FrameRecord(tmp_path, camera, now=Clock(), background=False).failed(
        "Levski", "no frame showed the map's search box", READING
    )
    assert camera.taken == 1
    [line] = _lines(tmp_path)
    assert (line["place"], line["why"]) == ("Levski", "no frame showed the map's search box")
    assert (tmp_path / str(line["file"])).read_bytes() == b"jpeg"
    assert line["size"] == [1568, 882]
    assert line["read"] == {
        "size": [2560, 1440],
        "words": [{"text": "STARMAP", "left": 10, "top": 10, "width": 80, "height": 20, "line": 0}],
    }


def test_no_picture_still_saves_why(tmp_path: Path) -> None:
    FrameRecord(tmp_path, Camera(fails=True), now=Clock(), background=False).failed(
        "Levski", "no frame showed the map's search box", None
    )
    [line] = _lines(tmp_path)
    assert (line["file"], line["size"], line["read"]) == ("", None, None)
    assert list(tmp_path.glob("*.jpg")) == []


def test_only_the_newest_are_kept(tmp_path: Path) -> None:
    frames = FrameRecord(tmp_path, Camera(), now=Clock(), keep=3, background=False)
    for n in range(5):
        frames.failed(f"Place {n}", "why", None)
    assert len(list(tmp_path.glob("*.jpg"))) == 3
    assert [line["place"] for line in _lines(tmp_path)] == ["Place 2", "Place 3", "Place 4"]


def test_the_record_is_beside_the_logs() -> None:
    assert paths.route_frames_dir().parent == paths.logs_dir()
