"""The console-close guard waits for shutdown before letting Windows end the process."""

import threading
import time

from tycho.runtime.console import CTRL_BREAK, CTRL_C, CTRL_CLOSE, CloseGuard


def test_closing_the_window_stops_tycho_and_waits_for_shutdown() -> None:
    stopped = threading.Event()
    guard = CloseGuard(stopped.set, wait=5)
    returned: list[bool] = []
    with guard:
        windows_thread = threading.Thread(
            target=lambda: returned.append(guard.on_event(CTRL_CLOSE))
        )
        windows_thread.start()
        assert stopped.wait(1)
        time.sleep(0.1)
        assert returned == []  # still holding Windows off while shutdown runs
    windows_thread.join(1)
    assert returned == [True]


def test_ctrl_break_is_handled_the_same_way() -> None:
    stopped = threading.Event()
    guard = CloseGuard(stopped.set, wait=0.05)
    assert guard.on_event(CTRL_BREAK)
    assert stopped.is_set()


def test_ctrl_c_is_left_to_python() -> None:
    stopped = threading.Event()
    assert not CloseGuard(stopped.set).on_event(CTRL_C)
    assert not stopped.is_set()


def test_the_wait_is_bounded() -> None:
    guard = CloseGuard(lambda: None, wait=0.05)
    started = time.monotonic()
    assert guard.on_event(CTRL_CLOSE)  # shutdown never finished, but Windows isn't held forever
    assert time.monotonic() - started < 1
