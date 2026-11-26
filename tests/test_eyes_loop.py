"""One reading process for a whole route (#661): the looping child and its parent side."""

import io
import subprocess
import sys
import textwrap
from typing import Any

import pytest
from PIL import Image

from tycho.eyes import CaptureError, child
from tycho.eyes.loop import WordsLoop
from tycho.eyes.words import Reading, Word, encode

READING = Reading(2560, 1440, (Word("SEARCH", 10, 10, 60, 20, 0),))

# A stand-in child: answers each line the way the looping child does. "crash" in
# argv makes it stop after the first answer; "hang" makes it never answer.
FAKE = textwrap.dedent(
    """
    import struct, sys
    answer = struct.Struct(">BI")
    sent = sys.argv[1].encode()
    out = sys.stdout.buffer
    for n, line in enumerate(sys.stdin.buffer):
        if "hang" in sys.argv:
            continue
        out.write(answer.pack(0, len(sent)) + sent)
        out.flush()
        if "crash" in sys.argv:
            sys.stderr.write("Windows fatal exception: access violation\\n")
            sys.exit(7)
    """
)


def _start(*flags: str) -> Any:
    started: list[list[str]] = []

    def start(args: list[str], **options: Any) -> subprocess.Popen[bytes]:
        started.append(args)
        assert "creationflags" in options  # no console window over the game
        command = [sys.executable, "-c", FAKE, encode(READING).decode(), *flags]
        return subprocess.Popen(  # noqa: S603 - this interpreter, running the script above
            command, stdin=options["stdin"], stdout=options["stdout"], stderr=options["stderr"]
        )

    start.started = started  # type: ignore[attr-defined]
    return start


def test_one_child_answers_every_look_of_a_route() -> None:
    start = _start()
    loop = WordsLoop(1234, start)
    try:
        assert loop.read() == READING
        assert loop.read() == READING
    finally:
        loop.close()
    [args] = start.started
    assert args == child.command(1234, loop=True)


def test_a_child_that_stops_is_a_capture_error_and_the_next_look_starts_another(
    caplog: pytest.LogCaptureFixture,
) -> None:
    start = _start("crash")
    loop = WordsLoop(1234, start)
    try:
        assert loop.read() == READING
        with pytest.raises(CaptureError, match="exit code 0x7"):
            loop.read()
        assert "access violation" in caplog.text  # the crash's last words are logged
        assert loop.read() == READING
    finally:
        loop.close()
    assert len(start.started) == 2


def test_a_child_that_hangs_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tycho.eyes.loop.FIRST_SECONDS", 0.5)
    loop = WordsLoop(1234, _start("hang"))
    with pytest.raises(CaptureError, match="longer than"):
        loop.read()
    loop.close()


def test_a_child_that_cant_start_is_a_capture_error() -> None:
    def missing(args: list[str], **_: Any) -> subprocess.Popen[bytes]:
        raise FileNotFoundError(args[0])

    with pytest.raises(CaptureError, match="couldn't start"):
        WordsLoop(1234, missing).read()


def test_the_looping_child_answers_each_line_and_says_why_when_it_cant() -> None:
    frames = iter([Image.new("RGB", (4, 4)), CaptureError("Windows sent no picture")])

    def frame(_window: int) -> Image.Image:
        got = next(frames)
        if isinstance(got, Exception):
            raise got
        return got

    out = io.BytesIO()
    code = child.main_words_loop(
        ["1234"], frame, lambda _image, _share: READING, io.BytesIO(b"read\nread\n"), out
    )
    assert code == 0
    sent = out.getvalue()
    status, length = child.ANSWER.unpack_from(sent)
    assert (status, sent[child.ANSWER.size : child.ANSWER.size + length]) == (0, encode(READING))
    rest = sent[child.ANSWER.size + length :]
    status, length = child.ANSWER.unpack_from(rest)
    assert (status, rest[child.ANSWER.size :]) == (1, b"Windows sent no picture")


def test_the_looping_child_reads_only_the_share_of_the_window_it_is_given() -> None:
    shares: list[float] = []

    def words(_image: Image.Image, share: float) -> Reading:
        shares.append(share)
        return READING

    def frame(_window: int) -> Image.Image:
        return Image.new("RGB", (4, 4))

    out = io.BytesIO()
    assert child.main_words_loop(["1234", "0.5"], frame, words, io.BytesIO(b"read\n"), out) == 0
    assert child.main_words_loop(["1234"], frame, words, io.BytesIO(b"read\n"), out) == 0
    assert shares == [0.5, 1.0]
    for bad in ("0", "2", "half"):
        code = child.main_words_loop(["1234", bad], frame, words, io.BytesIO(b"read\n"), out)
        assert code == child.NO_PICTURE


def test_a_loop_asks_its_child_for_the_packs_share() -> None:
    start = _start()
    loop = WordsLoop(1234, start, share=0.5)
    try:
        assert loop.read() == READING
    finally:
        loop.close()
    [args] = start.started
    assert args[-2:] == ["1234", "0.5"]
    assert child.command(1234, loop=True)[-1] == "1234"  # the whole window: no share


def test_the_installed_app_starts_the_looping_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert child.command(1234, loop=True)[1:] == [child.LOOP_FLAG, "1234"]
