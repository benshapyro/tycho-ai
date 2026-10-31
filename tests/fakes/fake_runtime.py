"""A transcriber and feedback sounds that follow a script, for runtime tests."""

import threading

from tycho.audio.clip import Samples


class FakeTranscriber:
    """Satisfies ``Transcriber``. Hears each scripted line in turn.

    A line that is an exception is raised instead. If ``hold`` is given, each
    call waits for it, so a test can act while transcription is in flight.
    """

    def __init__(self, *lines: str | Exception, hold: threading.Event | None = None) -> None:
        self._lines = list(lines)
        self.hold = hold
        self.calls = 0
        self.seconds: list[float] = []
        """How much audio each call was given."""
        self.started = threading.Event()

    def transcribe(self, samples: Samples, samplerate: int) -> str:
        self.calls += 1
        self.seconds.append(len(samples) / samplerate)
        self.started.set()
        if self.hold is not None:
            self.hold.wait(5)
        line = self._lines.pop(0)
        if isinstance(line, Exception):
            raise line
        return line


class FakeFeedback:
    """Satisfies ``Feedback``. Records "done" and "not_done" in order."""

    def __init__(self) -> None:
        self.played: list[str] = []

    def done(self) -> None:
        self.played.append("done")

    def not_done(self) -> None:
        self.played.append("not_done")


class FakeGameState:
    """Satisfies ``GameState``. Reports whatever facts the test sets."""

    def __init__(self, *facts: str) -> None:
        self.current = frozenset(facts)
        self.calls = 0

    def facts(self) -> frozenset[str]:
        self.calls += 1
        return self.current
