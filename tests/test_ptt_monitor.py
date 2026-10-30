from tests.fakes.fake_input import FakeInputSource
from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.monitor import Edge, PushToTalk, capture_binding

SIDE = Binding(DeviceKind.MOUSE, 0x05)
LEFT = Binding(DeviceKind.MOUSE, 0x01)
ENTER = Binding(DeviceKind.KEYBOARD, 0x0D)
STICK = Binding(DeviceKind.JOYSTICK, 4, "vkb-right")


def test_reports_press_and_release_once_each() -> None:
    mouse = FakeInputSource()
    ptt = PushToTalk(SIDE, [mouse])

    assert ptt.poll() is None
    mouse.held.add(SIDE)
    assert ptt.poll() is Edge.PRESSED
    assert ptt.poll() is None
    mouse.held.clear()
    assert ptt.poll() is Edge.RELEASED
    assert ptt.poll() is None


def test_ignores_other_buttons() -> None:
    mouse = FakeInputSource(LEFT, ENTER)
    assert PushToTalk(SIDE, [mouse]).poll() is None


def test_reads_whichever_source_owns_the_binding() -> None:
    keyboard_mouse, sticks = FakeInputSource(), FakeInputSource(STICK)
    assert PushToTalk(STICK, [keyboard_mouse, sticks]).poll() is Edge.PRESSED


class _Script:
    """Drives a fake source through a sequence of states, one per poll."""

    def __init__(self, source: FakeInputSource, states: list[set[Binding]]) -> None:
        self.source, self.states = source, states
        self.time = 0.0

    def sleep(self, seconds: float) -> None:
        self.time += seconds
        if self.states:
            self.source.held = self.states.pop(0)


def test_capture_returns_the_first_new_press() -> None:
    source = FakeInputSource()
    script = _Script(source, [set(), {SIDE}])
    assert capture_binding([source], sleep=script.sleep, clock=lambda: script.time) == SIDE


def test_capture_ignores_a_key_held_at_start_until_released() -> None:
    source = FakeInputSource(ENTER)
    script = _Script(source, [{ENTER}, set(), {ENTER}])
    assert capture_binding([source], sleep=script.sleep, clock=lambda: script.time) == ENTER
    assert not script.states


def test_capture_skips_ignored_buttons() -> None:
    source = FakeInputSource()
    script = _Script(source, [{LEFT}, {LEFT, SIDE}])
    found = capture_binding(
        [source], ignore=frozenset({LEFT}), sleep=script.sleep, clock=lambda: script.time
    )
    assert found == SIDE


def test_capture_times_out() -> None:
    source = FakeInputSource()
    script = _Script(source, [])
    found = capture_binding(
        [source], timeout=0.05, poll_interval=0.01, sleep=script.sleep, clock=lambda: script.time
    )
    assert found is None
