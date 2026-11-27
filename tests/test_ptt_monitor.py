import pytest

from tests.fakes.fake_input import FakeInputSource
from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.devices import rebind
from tycho.ptt.monitor import Edge, PushToTalk, capture_binding
from tycho.start import run as start_run

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


def test_either_of_two_buttons_holds_push_to_talk() -> None:
    """#621: a mouse button and a stick button, held the same way."""
    mouse, sticks = FakeInputSource(), FakeInputSource()
    ptt = PushToTalk([SIDE, STICK], [mouse, sticks])
    sticks.held.add(STICK)
    assert ptt.poll() is Edge.PRESSED
    mouse.held.add(SIDE)  # the other one too, then let go of the first: still one press
    sticks.held.clear()
    assert ptt.poll() is None
    mouse.held.clear()
    assert ptt.poll() is Edge.RELEASED


class _Closing(FakeInputSource):
    def __init__(self, *held: Binding) -> None:
        super().__init__(*held)
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_buttons_chosen_again_are_listened_to_from_the_next_poll() -> None:
    """#646: a stick button added in the app while Tycho runs works at once, and the old
    sources are let go on the polling thread (SDL's rule)."""
    mouse = _Closing()
    ptt = PushToTalk(SIDE, [mouse])
    sticks = FakeInputSource(STICK)
    ptt.rebind([SIDE, STICK], [FakeInputSource(), sticks])
    assert mouse.closed == 0  # not until the poll, on the poll's thread
    assert ptt.poll() is Edge.PRESSED
    assert mouse.closed == 1
    assert ptt.bindings == (SIDE, STICK)
    sticks.held.clear()
    assert ptt.poll() is Edge.RELEASED


def test_a_button_held_as_it_is_removed_is_let_go() -> None:
    mouse = FakeInputSource(SIDE)
    ptt = PushToTalk([SIDE, STICK], [mouse])
    assert ptt.poll() is Edge.PRESSED
    ptt.rebind([STICK], [FakeInputSource()])
    assert ptt.poll() is Edge.RELEASED  # never stuck listening
    assert ptt.poll() is None


def test_buttons_chosen_again_need_one() -> None:
    ptt = PushToTalk(SIDE, [FakeInputSource()])
    with pytest.raises(ValueError, match="at least one button"):
        ptt.rebind([], [FakeInputSource()])
    assert not rebind(ptt, None, "joystick:vkb-l:3")  # a second alone: the old one stays
    assert ptt.bindings == (SIDE,)


class _Broken(FakeInputSource):
    """A stick source SDL won't start for: every read raises."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def is_pressed(self, binding: Binding) -> bool:
        self.reads += 1
        raise OSError("SDL2.dll not found")

    def close(self) -> None:
        raise OSError("SDL was never set up")


def test_a_stick_that_cant_be_read_doesnt_keep_tycho_listening(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#676: the mouse button was seen, but once it was let go the stick's read raised,
    so the release never came and Tycho kept listening."""
    mouse, sticks = FakeInputSource(SIDE), _Broken()
    ptt = PushToTalk([SIDE, STICK], [mouse, sticks])
    assert ptt.poll() is Edge.PRESSED
    mouse.held.clear()
    assert ptt.poll() is Edge.RELEASED
    assert ptt.poll() is None
    assert sticks.reads == 1  # then left alone for RETRY_SECONDS
    failed = [r for r in caplog.records if "Reading push-to-talk" in r.getMessage()]
    assert len(failed) == 1  # once, not every 5 ms


def test_a_stick_that_cant_be_read_is_tried_again_each_second() -> None:
    """Review of #697: SDL was set up again on every 5 ms poll for the whole session."""
    now = [0.0]
    mouse, sticks = FakeInputSource(), _Broken()
    ptt = PushToTalk([SIDE, STICK], [mouse, sticks], clock=lambda: now[0])
    for _ in range(100):
        ptt.poll()
        now[0] += 0.005
    assert sticks.reads == 1  # 0.5 s of polls: one try
    now[0] += 0.6
    ptt.poll()
    assert sticks.reads == 2


def test_a_source_that_fails_to_close_doesnt_keep_the_others_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken, mouse = _Broken(), _Closing()
    ptt = PushToTalk(SIDE, [broken, mouse])
    ptt.rebind([SIDE], [FakeInputSource(SIDE)])
    assert ptt.poll() is Edge.PRESSED
    assert mouse.closed == 1
    assert any("Closing push-to-talk" in r.getMessage() for r in caplog.records)


def test_the_apps_buttons_are_listened_to_as_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    """#676 item 2: the app's (first, second) reaches the running push-to-talk, a
    second of None is only the first, and anything else is refused, not guessed at."""

    def sources(*_bindings: Binding) -> list[FakeInputSource]:
        return [FakeInputSource(SIDE)]

    monkeypatch.setattr("tycho.ptt.devices.input_sources", sources)
    rebind_ = start_run._rebind  # pyright: ignore[reportPrivateUsage]
    ptt = PushToTalk(LEFT, [FakeInputSource()])
    rebind_(ptt, ("mouse:0x05", "joystick:vkb-right:4"))
    assert ptt.poll() is Edge.PRESSED
    assert ptt.bindings == (SIDE, STICK)
    rebind_(ptt, ("mouse:0x05", None))
    ptt.poll()
    assert ptt.bindings == (SIDE,)
    for wrong in ("mouse:0x05", None, (5, None), ("mouse:0x05",)):
        with pytest.raises(TypeError, match="push-to-talk buttons"):
            rebind_(ptt, wrong)
    with pytest.raises(ValueError, match="first button"):
        rebind_(ptt, ("", "mouse:0x05"))
    assert ptt.bindings == (SIDE,)


def test_push_to_talk_needs_a_button() -> None:
    with pytest.raises(ValueError, match="at least one button"):
        PushToTalk([], [FakeInputSource()])


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
