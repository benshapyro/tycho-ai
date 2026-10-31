from collections.abc import Callable

import numpy as np
import pytest

from tycho.audio.clip import Samples
from tycho.feedback import LOUDEST, SAMPLERATE, SoundFeedback, done_sound, not_done_sound


class FakePlayer:
    def __init__(self, fail: bool = False) -> None:
        self.played: list[Samples] = []
        self.fail = fail

    def play(self, samples: Samples, rate: int) -> None:
        if self.fail:
            raise OSError("no output device")
        assert rate == SAMPLERATE
        self.played.append(samples)


def _dominant_hz(samples: Samples) -> float:
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.fft.rfftfreq(len(samples), 1 / SAMPLERATE)[np.argmax(spectrum)])


@pytest.mark.parametrize(("make", "longest"), [(done_sound, 0.1), (not_done_sound, 0.3)])
def test_sounds_are_short_and_never_clip(make: Callable[[], Samples], longest: float) -> None:
    sound = make()
    assert len(sound) / SAMPLERATE <= longest
    assert np.max(np.abs(sound)) == pytest.approx(1.0, abs=1e-3)
    assert abs(float(sound[-1])) < 0.01  # faded, so it never ends on a click


def test_not_done_is_much_lower_than_done() -> None:
    assert _dominant_hz(done_sound()) > 1_500
    assert _dominant_hz(not_done_sound()) < 400


def test_each_outcome_plays_its_own_sound() -> None:
    player = FakePlayer()
    feedback = SoundFeedback(player, volume=1.0)
    feedback.done()
    feedback.not_done()
    assert len(player.played) == 2
    assert len(player.played[0]) < len(player.played[1])  # the click is shorter
    assert player.played[0].dtype == np.float32


@pytest.mark.parametrize("volume", [0.25, 0.5, 1.0, 3.0])
def test_volume_sets_the_peak_and_never_exceeds_the_limit(volume: float) -> None:
    player = FakePlayer()
    SoundFeedback(player, volume).done()
    peak = float(np.max(np.abs(player.played[0])))
    assert peak == pytest.approx(LOUDEST * min(volume, 1.0) ** 1.5, rel=1e-3)
    assert peak <= LOUDEST


def test_volume_zero_plays_nothing() -> None:
    player = FakePlayer()
    feedback = SoundFeedback(player, volume=0.0)
    feedback.done()
    feedback.not_done()
    assert player.played == []


def test_a_missing_output_device_never_stops_tycho() -> None:
    feedback = SoundFeedback(FakePlayer(fail=True), volume=0.5)
    feedback.done()  # logged, not raised
    feedback.not_done()
