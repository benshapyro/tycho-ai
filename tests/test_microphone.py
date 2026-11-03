"""The real microphone class, with PortAudio's stream faked: a microphone that goes
away mid-session says so, and comes back when it can (audit, 2026-11-03)."""

from typing import Any, ClassVar

import numpy as np
import pytest

from tycho.audio import microphone
from tycho.audio.microphone import BLOCK_FRAMES, Microphone
from tycho.audio.source import AudioError


class FakeStream:
    """Stands in for ``sounddevice.InputStream``."""

    opened: ClassVar[list[FakeStream]] = []
    fail_to_open = False

    def __init__(self, *, callback: Any, **_: Any) -> None:
        if FakeStream.fail_to_open:
            raise microphone.sd.PortAudioError("Device unavailable")
        self.callback = callback
        self.active = False
        self.closed = False
        FakeStream.opened.append(self)

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True
        self.active = False

    def block(self) -> None:
        """One 20 ms block of sound arrives."""
        data = np.full((BLOCK_FRAMES, 1), 0.1, dtype=np.float32)
        self.callback(data, BLOCK_FRAMES, None, microphone.sd.CallbackFlags())


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def mic(monkeypatch: pytest.MonkeyPatch) -> tuple[Microphone, Clock, list[str]]:
    FakeStream.opened = []
    FakeStream.fail_to_open = False
    monkeypatch.setattr(microphone.sd, "InputStream", FakeStream)
    clock = Clock()
    rescans: list[str] = []
    m = Microphone(clock=clock, rescan=lambda: rescans.append("rescan"))
    m.__enter__()
    return m, clock, rescans


def test_a_working_microphone_records_without_reopening(
    mic: tuple[Microphone, Clock, list[str]],
) -> None:
    m, clock, rescans = mic
    stream = FakeStream.opened[-1]
    m.start()
    for _ in range(10):
        clock.now += 0.02
        stream.block()
    assert len(m.stop()) >= 10 * BLOCK_FRAMES
    assert rescans == []
    assert len(FakeStream.opened) == 1


def test_a_microphone_that_went_quiet_is_reopened_on_the_next_press(
    mic: tuple[Microphone, Clock, list[str]],
) -> None:
    m, clock, rescans = mic
    old = FakeStream.opened[-1]
    clock.now += 5.0  # unplugged: no blocks since
    m.start()
    assert rescans == ["rescan"]
    assert old.closed
    new = FakeStream.opened[-1]
    assert new is not old
    assert new.active
    clock.now += 0.02
    new.block()
    m.stop()  # works again


def test_a_microphone_that_cant_be_reopened_says_so(
    mic: tuple[Microphone, Clock, list[str]],
) -> None:
    m, clock, _ = mic
    clock.now += 5.0
    FakeStream.fail_to_open = True
    with pytest.raises(AudioError, match="plugged in"):
        m.start()


def test_a_microphone_that_dies_mid_press_says_so(
    mic: tuple[Microphone, Clock, list[str]],
) -> None:
    m, clock, _ = mic
    m.start()
    clock.now += 3.0  # held for 3 s with nothing arriving
    with pytest.raises(AudioError, match="stopped sending sound"):
        m.stop()


def test_a_stream_portaudio_stopped_counts_as_gone(
    mic: tuple[Microphone, Clock, list[str]],
) -> None:
    m, _, rescans = mic
    FakeStream.opened[-1].active = False
    m.start()
    assert rescans == ["rescan"]
