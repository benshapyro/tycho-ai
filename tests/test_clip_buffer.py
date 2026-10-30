import numpy as np

from tests.fakes.fake_audio import FakeAudioSource
from tycho.audio.clip import ClipBuffer
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
