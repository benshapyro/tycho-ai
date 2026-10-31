from pathlib import Path

from tycho.game.gamelog import QUANTUM_CALIBRATED, GameLog

# Lines as the game writes them (Game.log, 2026-10-31), shortened.
HEADER = "<2026-10-31T03:00:19.774Z> Log started on Sat Oct 31 03:00:19 2026\n"
STARTED = (
    "<2026-10-31T06:20:43.074Z> [Notice] <SHUDEvent_OnNotification> Added notification "
    '"Quantum Travel Calibration Started By SpaceEdd: " [107] to queue. New queue size: 1\n'
)
COMPLETE = (
    "<2026-10-31T06:20:45.555Z> [Notice] <SHUDEvent_OnNotification> Added notification "
    '"Quantum Travel Calibration Complete By SpaceEdd: " [108] to queue. New queue size: 2\n'
)
QUEUED = '<2026-10-31T06:20:45.555Z>    "Quantum Travel Calibration Complete By SpaceEdd: " [108]\n'
LEFT_SEAT = (
    "<2026-10-31T07:42:26.518Z> [Notice] <Vehicle Control Flow> CVehicleMovementBase::"
    "ClearDriver: Local client node [1] releasing control token for 'DRAK_Golem_2' [2]\n"
)


def _log(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "Game.log"
    path.write_text(HEADER + "".join(lines), "utf-8", newline="")
    return path


def _append(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as file:
        file.write("".join(lines))


def test_calibration_complete_means_calibrated(tmp_path: Path) -> None:
    log = GameLog(_log(tmp_path, STARTED, COMPLETE))
    assert log.facts() == {QUANTUM_CALIBRATED}
    assert log.readable


def test_a_new_calibration_or_leaving_the_seat_clears_it(tmp_path: Path) -> None:
    path = _log(tmp_path, COMPLETE)
    log = GameLog(path)
    assert QUANTUM_CALIBRATED in log.facts()
    _append(path, STARTED)
    assert QUANTUM_CALIBRATED not in log.facts()
    _append(path, COMPLETE, LEFT_SEAT)
    assert QUANTUM_CALIBRATED not in log.facts()


def test_the_notification_queue_listing_is_not_an_event(tmp_path: Path) -> None:
    assert GameLog(_log(tmp_path, QUEUED)).facts() == frozenset()


def test_reads_only_new_lines_and_waits_for_half_written_ones(tmp_path: Path) -> None:
    path = _log(tmp_path)
    log = GameLog(path)
    assert log.facts() == frozenset()
    _append(path, COMPLETE[:40])
    assert log.facts() == frozenset()
    _append(path, COMPLETE[40:])
    assert log.facts() == {QUANTUM_CALIBRATED}


def test_a_game_restart_forgets_the_last_session(tmp_path: Path) -> None:
    path = _log(tmp_path, COMPLETE, "x" * 500 + "\n")
    log = GameLog(path)
    assert QUANTUM_CALIBRATED in log.facts()
    # The game starts a fresh Game.log, longer than the old one before we look again.
    path.write_text(HEADER.replace("03:00", "09:15") + "y" * 1000 + "\n", "utf-8", newline="")
    assert log.facts() == frozenset()


def test_no_log_means_nothing_is_known(tmp_path: Path) -> None:
    log = GameLog(tmp_path / "Game.log")
    assert log.facts() == frozenset()
    assert not log.readable
