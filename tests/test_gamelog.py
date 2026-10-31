from pathlib import Path

from tycho.game.gamelog import QUANTUM_ROUTE, GameLog

# Lines as the game writes them (the author's Game.log, 2026-10-30 and 31), shortened.
HEADER = "<2026-10-31T14:30:14.950Z> Log started on Sat Oct 31 14:30:14 2026\n"
PLOTTED = (
    "<2026-10-31T18:49:13.428Z> [Notice] <Calculate Route> [ItemNavigation][CL][16468] | NOT AUTH"
    " | DRAK_Golem_909917463059[909917463059]|CSCItemNavigation::CalculateRoute|Successfully "
    "calculated route to ObjectContainer_Lorville_City fuel estimate 14.193884 [Team_CGP4]\n"
)
ARRIVED = (
    "<2026-10-31T18:50:39.132Z> [Notice] <Quantum Drive Arrived - Arrived at Final Destination>"
    " [ItemNavigation][CL][16468] | NOT AUTH | DRAK_Golem_909917463059[909917463059]|"
    "CSCItemNavigation::OnQuantumDriveArrived|Quantum Drive has arrived at final destination\n"
)
LEFT_SEAT = (
    "<2026-10-31T07:42:26.518Z> [Notice] <Vehicle Control Flow> CVehicleMovementBase::"
    "ClearDriver: Local client node [1] releasing control token for 'DRAK_Golem_2' [2]\n"
)
# Only other players' calibrations are logged; the player's own never are.
OTHERS_CALIBRATION = (
    "<2026-10-31T06:20:45.555Z> [Notice] <SHUDEvent_OnNotification> Added notification "
    '"Quantum Travel Calibration Complete By SpaceEdd: " [108] to queue. New queue size: 2\n'
)


def _log(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "Game.log"
    path.write_text(HEADER + "".join(lines), "utf-8", newline="")
    return path


def _append(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as file:
        file.write("".join(lines))


def test_a_plotted_route_is_known(tmp_path: Path) -> None:
    log = GameLog(_log(tmp_path, PLOTTED))
    assert log.facts() == {QUANTUM_ROUTE}
    assert log.readable


def test_arriving_or_leaving_the_seat_clears_it(tmp_path: Path) -> None:
    path = _log(tmp_path, PLOTTED)
    log = GameLog(path)
    assert QUANTUM_ROUTE in log.facts()
    _append(path, ARRIVED)
    assert QUANTUM_ROUTE not in log.facts()
    _append(path, PLOTTED, LEFT_SEAT)
    assert QUANTUM_ROUTE not in log.facts()


def test_another_players_calibration_means_nothing(tmp_path: Path) -> None:
    assert GameLog(_log(tmp_path, OTHERS_CALIBRATION)).facts() == frozenset()


def test_reads_only_new_lines_and_waits_for_half_written_ones(tmp_path: Path) -> None:
    path = _log(tmp_path)
    log = GameLog(path)
    assert log.facts() == frozenset()
    _append(path, PLOTTED[:200])
    assert log.facts() == frozenset()
    _append(path, PLOTTED[200:])
    assert log.facts() == {QUANTUM_ROUTE}


def test_a_game_restart_forgets_the_last_session(tmp_path: Path) -> None:
    path = _log(tmp_path, PLOTTED, "x" * 500 + "\n")
    log = GameLog(path)
    assert QUANTUM_ROUTE in log.facts()
    # The game starts a fresh Game.log, longer than the old one before we look again.
    path.write_text(HEADER.replace("14:30", "19:15") + "y" * 1000 + "\n", "utf-8", newline="")
    assert log.facts() == frozenset()


def test_no_log_means_nothing_is_known(tmp_path: Path) -> None:
    log = GameLog(tmp_path / "Game.log")
    assert log.facts() == frozenset()
    assert not log.readable
