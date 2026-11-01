import numpy as np

from tests.fakes.fake_audio import FakeAudioSource
from tycho.audio.clip import ClipBuffer, trailing_quiet_seconds, trim_silence
from tycho.audio.source import AudioSource


def _block(value: float, frames: int = 10) -> np.ndarray[tuple[int], np.dtype[np.float32]]:
    return np.full(frames, value, dtype=np.float32)


def test_clip_starts_with_preroll_then_recorded_audio() -> None:
    buffer = ClipBuffer(samplerate=100, preroll_seconds=0.2)  # 20 frames of pre-roll
    for value in (1, 2, 3, 4):
        buffer.feed(_block(value))
    buffer.start()
    buffer.feed(_block(5))
    clip = buffer.stop()

    assert list(np.unique(clip)) == [3, 4, 5]
    assert len(clip) == 30


def test_audio_outside_recording_is_discarded() -> None:
    buffer = ClipBuffer(samplerate=100, preroll_seconds=0.0)
    buffer.feed(_block(1))
    buffer.start()
    buffer.feed(_block(2))
    first = buffer.stop()
    buffer.feed(_block(3))
    buffer.start()
    buffer.feed(_block(4))

    assert list(np.unique(first)) == [2]
    assert list(np.unique(buffer.stop())) == [4]


def test_fake_audio_source_satisfies_the_protocol_and_keeps_preroll() -> None:
    source: AudioSource = FakeAudioSource(samplerate=100, preroll_seconds=0.1)
    with source:
        source.hear(_block(1, 20), block=10)  # only the last 10 frames survive as pre-roll
        source.start()
        source.hear(_block(2, 20), block=10)
        clip = source.stop()
    assert len(clip) == 30
    assert list(np.unique(clip)) == [1, 2]


def test_stop_without_audio_returns_an_empty_clip() -> None:
    buffer = ClipBuffer(samplerate=100, preroll_seconds=0.1)
    buffer.start()
    assert buffer.stop().size == 0
    assert not buffer.recording


RATE = 16_000


def _sound(seconds: float, level: float) -> np.ndarray[tuple[int], np.dtype[np.float32]]:
    return np.full(round(RATE * seconds), level, dtype=np.float32)


def _clip(*parts: tuple[float, float]) -> np.ndarray[tuple[int], np.dtype[np.float32]]:
    return np.concatenate([_sound(seconds, level) for seconds, level in parts])


def test_trimming_keeps_the_speech_and_a_margin() -> None:
    # A quiet hum (not digital silence), speech, then the hum again.
    clip = _clip((0.5, 0.001), (0.6, 0.1), (0.3, 0.001))
    trimmed = trim_silence(clip, RATE, margin=0.08)
    assert len(trimmed) / RATE == np.float64(0.6 + 0.16)
    assert trimmed.max() == np.float32(0.1)


def test_trimming_leaves_a_clip_with_no_speech_alone() -> None:
    for clip in (_sound(1.0, 0.0), _sound(1.0, 0.1), _sound(0.01, 0.1)):
        assert len(trim_silence(clip, RATE)) == len(clip)


def test_soft_speech_on_a_gated_microphone_is_kept() -> None:
    # Digital silence, then speech only 20 dB above -70 dBFS: still speech.
    clip = _clip((0.4, 0.0), (0.5, 0.003), (0.2, 0.0))
    assert len(trim_silence(clip, RATE, margin=0.0)) / RATE == np.float64(0.5)


def test_how_long_the_end_has_been_quiet() -> None:
    assert trailing_quiet_seconds(_clip((0.5, 0.1), (0.1, 0.0)), RATE) == 0.1
    assert trailing_quiet_seconds(_clip((0.1, 0.0), (0.5, 0.1)), RATE) == 0.0
    assert trailing_quiet_seconds(np.zeros(0, dtype=np.float32), RATE) == 0.0


def test_quiet_is_measured_on_the_clip_being_recorded() -> None:
    buffer = ClipBuffer(samplerate=RATE, preroll_seconds=0.2)
    buffer.feed(_sound(0.2, 0.1))  # loud pre-roll
    assert buffer.quiet_seconds() == 0.0  # not recording
    buffer.start()
    buffer.feed(_sound(0.1, 0.0))
    assert buffer.quiet_seconds() == 0.1
    buffer.feed(_sound(0.02, 0.1))
    assert buffer.quiet_seconds() == 0.0
