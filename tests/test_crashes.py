"""Native crashes are recorded, and the next start says so (#296)."""

import faulthandler
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tycho import crashes, doctor, paths
from tycho.settings import Settings
from tycho.start import run as start_run

CRASH = """
import faulthandler, sys
from pathlib import Path
from tycho import crashes
if sys.platform == "win32":  # no "this program stopped working" box on the test PC
    import msvcrt
    msvcrt.SetErrorMode(msvcrt.SEM_NOGPFAULTERRORBOX)
crashes.watch(Path(sys.argv[1]))
faulthandler._sigsegv()
"""
CLEAN = """
import sys
from pathlib import Path
from tycho import crashes
assert crashes.watch(Path(sys.argv[1])) is None
"""


def _tycho(script: str, folder: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this interpreter, running a script above
        [sys.executable, "-c", script, str(folder)], capture_output=True, text=True, check=False
    )


def test_a_native_crash_is_found_at_the_next_start(tmp_path: Path) -> None:
    # Before #296 nothing turned faulthandler on: the process ended with no trace.
    died = _tycho(CRASH, tmp_path)
    assert died.returncode != 0
    assert died.stderr == ""  # written to the file, not to a terminal that isn't there

    found = crashes.watch(tmp_path)

    assert found is not None
    assert found.parent == tmp_path
    assert found.name.startswith("crash-")
    text = found.read_text(encoding="utf-8")
    assert "Segmentation fault" in text or "Windows fatal exception" in text
    assert "<module>" in text  # what each thread was doing
    assert crashes.watch(tmp_path) is None  # said once
    assert crashes.past(tmp_path) == [found]  # and kept


def test_a_clean_stop_leaves_nothing_to_find(tmp_path: Path) -> None:
    done = _tycho(CLEAN, tmp_path)
    assert done.returncode == 0, done.stderr
    assert list(tmp_path.iterdir()) == []
    assert crashes.watch(tmp_path) is None


def test_a_clean_stop_removes_what_a_handled_exception_wrote(tmp_path: Path) -> None:
    # .NET handles some Windows exceptions after faulthandler has written them down.
    crashes.watch(tmp_path)
    assert faulthandler.is_enabled()
    (tmp_path / crashes.LIVE).write_text("Windows fatal exception: access violation\n")
    crashes.stop()
    assert not (tmp_path / crashes.LIVE).exists()
    assert crashes.watch(tmp_path) is None


def test_a_tycho_ended_from_outside_is_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / crashes.LIVE).write_text("")  # Task Manager, or the power went
    assert crashes.watch(tmp_path) is None
    assert crashes.past(tmp_path) == []


def test_only_the_last_few_crashes_are_kept(tmp_path: Path) -> None:
    for day in range(1, 9):
        (tmp_path / f"crash-2026-11-{day:02d}-120000.txt").write_text("Fatal Python error\n")
    (tmp_path / crashes.LIVE).write_text("Fatal Python error: Segmentation fault\n")
    found = crashes.watch(tmp_path)
    kept = crashes.past(tmp_path)
    assert len(kept) == crashes.KEEP
    assert kept[0] == found
    assert kept[-1].name == "crash-2026-11-05-120000.txt"


def test_tycho_check_says_so_for_a_few_days(tmp_path: Path) -> None:
    record = tmp_path / "crash-2026-11-14-210300.txt"
    record.write_text("Fatal Python error: Segmentation fault\n")
    at = time.mktime((2026, 11, 14, 21, 3, 0, 0, 0, -1))
    os.utime(record, (at, at))

    found = crashes.finding(tmp_path, now=at + 3600)
    assert found is not None
    assert found.level == "warn"
    assert "14 November at 21:03" in found.detail
    assert str(record) in found.detail
    assert crashes.finding(tmp_path, now=at + 4 * 24 * 3600) is None
    assert crashes.finding(tmp_path / "nothing-here") is None


def test_the_checks_include_a_recent_crash() -> None:
    assert "Last crash" not in [f.what for f in doctor.checks(Settings(), quick=True, ask=False)]
    paths.logs_dir().mkdir(parents=True)
    (paths.logs_dir() / time.strftime("crash-%Y-%m-%d-%H%M%S.txt")).write_text("Fatal\n")
    found = [f for f in doctor.checks(Settings(), quick=True, ask=False) if f.what == "Last crash"]
    assert len(found) == 1


def test_the_next_start_tells_the_player_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    told: list[list[str]] = []

    def start(_current: Settings, *, notices: list[str], **_options: object) -> int:
        told.append(list(notices))
        return 0

    def no_update_check(_current: Settings) -> None:
        pass

    monkeypatch.setattr(start_run, "start_update_check", no_update_check)
    monkeypatch.setattr(start_run, "_start", start)
    (tmp_path / crashes.LIVE).write_text("Fatal Python error: Segmentation fault\n")
    log_file = tmp_path / "tycho.jsonl"

    with caplog.at_level("WARNING", logger="tycho"):
        assert start_run._run(Settings(), log_file, window=False) == 0  # pyright: ignore[reportPrivateUsage]
        assert start_run._run(Settings(), log_file, window=False) == 0  # pyright: ignore[reportPrivateUsage]

    kept = crashes.past(tmp_path)
    assert len(kept) == 1
    assert told == [[crashes.STOPPED.format(file=kept[0])], []]  # shown on the Tile, once
    assert caplog.text.count("stopped unexpectedly") == 1
    assert faulthandler.is_enabled()  # and this run is being recorded
