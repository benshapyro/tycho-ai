"""One child process that reads the game window's words on request, for one route (#661).

Starting the capture's child (``eyes.child``) takes 1 to 2 s: loading Python, then the
WinRT modules. A route's local checks start one per look, which left only about two
looks inside the 3 s the map has to show its search box (ADR 3), and the check could
miss a map that opened a moment late. ``WordsLoop`` starts the child once, as the route
begins (so it starts while the map is opening), asks it for a new frame's words each
time, and closes it when the route ends. A look is then one frame and Windows' text
recognition: a few hundred milliseconds.

Everything else stays as ADR 14's "local check" says: the frame never leaves the child,
only the words come back, and a crash ends only the child. Then the next look starts a
new one.
"""

import contextlib
import logging
import queue
import subprocess
import tempfile
import threading
from collections.abc import Callable
from typing import IO

from tycho.eyes import CaptureError, child
from tycho.eyes.words import Reading, decode

log = logging.getLogger(__name__)

FIRST_SECONDS = child.SECONDS
"""The first look: the child is starting, as for a one-off look."""
NEXT_SECONDS = 7.0
"""A later look: one frame and text recognition, usually well under a second, but up to
the child's own limits (1.5 s for the frame, 1.5 s to copy it, 3 s to read it) on a busy
PC (review of #665)."""
ERROR_BYTES = 4000
"""The end of a stopped child's stderr (a crash's stack) kept for the log, as #558 does."""
CLOSE_SECONDS = 1.0

type Start = Callable[..., subprocess.Popen[bytes]]


class WordsLoop:
    """A looping child for ``window``. Blocking. Not thread-safe: one route at a time."""

    def __init__(self, window: int, start: Start = subprocess.Popen, share: float = 1.0) -> None:
        self.window = window
        self.share = share
        """How much of the window, from its left, the child reads (the pack's
        ``read_width``)."""
        self._start = start
        self._process: subprocess.Popen[bytes] | None = None
        self._answers: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
        self._asked = 0
        self._errors: IO[bytes] | None = None

    def open(self) -> None:
        """Start the child, if it isn't running: it then loads while other work goes on."""
        if self._process is not None:
            return
        errors = tempfile.TemporaryFile()  # noqa: SIM115 - open for the child's life, see close()
        # A file, not a pipe: it never fills up and blocks the child.
        try:
            process = self._start(
                child.command(self.window, loop=True, share=self.share),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            errors.close()
            raise CaptureError(f"the capture couldn't start ({e})") from e
        self._process, self._asked, self._errors = process, 0, errors
        self._answers = queue.Queue()
        if process.stdout is None or process.stdin is None:
            self.close()
            raise CaptureError("the capture started with no pipes")
        threading.Thread(
            target=_listen, args=(process.stdout, self._answers), name="words-loop", daemon=True
        ).start()

    def read(self) -> Reading:
        """The words on a new frame. ``CaptureError`` if there's none; a child that
        stopped or hangs is closed, and the next read starts another."""
        self.open()
        process = self._process
        if process is None or process.stdin is None:
            raise CaptureError("the capture didn't start")
        seconds = FIRST_SECONDS if self._asked == 0 else NEXT_SECONDS
        self._asked += 1
        try:
            process.stdin.write(b"read\n")
            process.stdin.flush()
        except OSError as e:
            self.close()
            raise CaptureError(f"the capture stopped ({e})") from e
        try:
            answer = self._answers.get(timeout=seconds)
        except queue.Empty:
            self.close()
            raise CaptureError(f"the capture took longer than {seconds:.0f} s") from None
        if answer is None:
            why = self._stopped()
            self.close()
            raise CaptureError(f"the capture stopped ({why})")
        status, sent = answer
        if status != 0:
            raise CaptureError(sent.decode("utf-8", "replace") or "no picture")
        return decode(sent)

    def _stopped(self) -> str:
        """Why the child ended: its exit code, with its last words logged (#558)."""
        process, errors = self._process, self._errors
        if process is None:
            return "no child"
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(CLOSE_SECONDS)
        said = ""
        if errors is not None:
            with contextlib.suppress(OSError, ValueError):
                errors.seek(0, 2)
                errors.seek(max(0, errors.tell() - ERROR_BYTES))
                said = errors.read().decode("utf-8", "replace").strip()
        code = process.returncode
        shown = f"exit code {code & 0xFFFFFFFF:#x}" if code is not None else "no exit code yet"
        log.warning("The map check's reader stopped (%s): %s", shown, said)
        return shown

    def close(self) -> None:
        """End the child: its stdin closes, and it stops; one that doesn't is killed."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(CLOSE_SECONDS)
        except OSError, subprocess.TimeoutExpired:
            process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(CLOSE_SECONDS)
        if process.stdout is not None:
            process.stdout.close()  # the listener's read ends with it
        if self._errors is not None:
            self._errors.close()
            self._errors = None


def _listen(out: IO[bytes], answers: queue.Queue[tuple[int, bytes] | None]) -> None:
    """The child's answers, one by one, until it ends (then None)."""
    try:
        while header := out.read(child.ANSWER.size):
            if len(header) < child.ANSWER.size:
                break
            status, length = child.ANSWER.unpack(header)
            answers.put((status, out.read(length)))
    except OSError, ValueError:
        pass
    answers.put(None)
