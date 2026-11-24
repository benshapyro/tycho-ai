"""Taking the picture in a process of its own (#558).

Copying a frame of Star Citizen's window out of the graphics card crashed Python with
an access violation inside Windows (``SoftwareBitmap.create_copy_from_surface_async``),
which no ``except`` can catch: in Tycho's own process, "take a look" would end the app
mid-game. So ``eyes.windows`` runs each capture in a short-lived child process: the same
program, started with ``FLAG`` (the installed app) or as ``python -m tycho.eyes.child``
(from source). If the child crashes, Tycho hears an exit code, says it can't see the game
window, and carries on.

The picture comes back on the child's stdout, in memory: never written to disk (ADR 14).
Its stderr, logged when a capture fails, holds only why (and a crash's stack), never the
picture.

With ``READ_FLAG`` the child reads the frame's words instead (``eyes.ocr``, #588), for
the route flow's local check: only the words and their boxes come back, and the frame
never leaves the child.
"""

import faulthandler
import logging
import struct
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tycho.eyes import CaptureError, Picture
from tycho.eyes.words import Reading, decode, encode

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger(__name__)

FLAG = "--capture-game-window"
"""The installed tycho.exe's hidden argument for one capture: never for players."""
READ_FLAG = "--read-game-window"
"""Its hidden argument for one reading of the window's words (#588)."""
_WORDS = "--words"
"""From source, ``python -m tycho.eyes.child --words <window>``."""
SECONDS = 8.0
"""Starting the child, loading the WinRT modules and one frame: usually 1 to 2 s."""
NO_PICTURE = 3
"""The child's exit code when there's no picture and it says why on stderr."""
_SIZE = struct.Struct(">II")


def command(window: int, words: bool = False) -> list[str]:
    """How to start the child for ``window`` (``words``: to read its words). In the
    installed app, the terminal program, tycho.exe, whose stdout works even when
    tycho-app.exe (no terminal) asks."""
    if getattr(sys, "frozen", False):
        flag = READ_FLAG if words else FLAG
        return [str(Path(sys.executable).with_name("tycho.exe")), flag, str(window)]
    return [sys.executable, "-m", "tycho.eyes.child", *([_WORDS] if words else []), str(window)]


Run = Callable[..., "subprocess.CompletedProcess[bytes]"]


def take(window: int, run: Run = subprocess.run) -> Picture:
    """One picture of ``window``, taken by a child process. Raises ``CaptureError``
    whatever went wrong in it, a crash included."""
    return read(_run(command(window), run))


def read_words(window: int, run: Run = subprocess.run) -> Reading:
    """The words on one frame of ``window``, read by a child process. Raises
    ``CaptureError`` whatever went wrong in it, a crash included."""
    return decode(_run(command(window, words=True), run))


def _run(args: list[str], run: Run) -> bytes:
    try:
        done = run(
            args,
            capture_output=True,
            timeout=SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as e:
        raise CaptureError(f"the capture took longer than {SECONDS:.0f} s") from e
    except OSError as e:
        raise CaptureError(f"the capture couldn't start ({e})") from e
    why = done.stderr.decode("utf-8", "replace").strip()
    if done.returncode == NO_PICTURE:
        raise CaptureError(why.splitlines()[-1] if why else "no picture")
    if done.returncode != 0:
        log.warning("The capture stopped (exit code %#x): %s", done.returncode & 0xFFFFFFFF, why)
        raise CaptureError(f"the capture stopped (exit code {done.returncode & 0xFFFFFFFF:#x})")
    return done.stdout


def write(picture: Picture) -> bytes:
    """``picture`` as the child sends it: its width and height, then the JPEG."""
    return _SIZE.pack(picture.width, picture.height) + picture.data


def read(sent: bytes) -> Picture:
    """What the child sent, back as a ``Picture``."""
    if len(sent) <= _SIZE.size:
        raise CaptureError("the capture sent no picture")
    width, height = _SIZE.unpack_from(sent)
    return Picture(sent[_SIZE.size :], width, height)


def main(argv: Sequence[str], frame: Callable[[int], Image.Image] | None = None) -> int:
    """The child: capture the window ``argv[0]`` names, and write it to stdout."""
    from tycho.eyes import shrink

    return _child(argv, frame, lambda image: write(shrink(image)))


def main_words(
    argv: Sequence[str],
    frame: Callable[[int], Image.Image] | None = None,
    words: Callable[[Image.Image], Reading] | None = None,
) -> int:
    """The child: read the words on the window ``argv[0]`` names, and write them (never
    the frame) to stdout."""
    if words is None:
        from tycho.eyes.ocr import read_words as words
    reader = words
    return _child(argv, frame, lambda image: encode(reader(image)))


def _child(
    argv: Sequence[str],
    frame: Callable[[int], Image.Image] | None,
    send: Callable[[Image.Image], bytes],
) -> int:
    faulthandler.enable()  # a crash's stack goes to stderr, for the parent's log
    if sys.platform == "win32":
        from tycho import win32

        win32.no_crash_box()  # no "stopped working" box over the game
    try:
        window = int(argv[0])
        if frame is None:
            from tycho.eyes.windows import frame_of

            frame = frame_of
        sent = send(frame(window))
    except (CaptureError, IndexError, ValueError) as e:
        sys.stderr.write(f"{e}\n")
        return NO_PICTURE
    except Exception as e:  # any other error still says why, and nothing crashes
        sys.stderr.write(f"{type(e).__name__}: {e}\n")
        return NO_PICTURE
    sys.stdout.buffer.write(sent)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == [_WORDS]:
        sys.exit(main_words(sys.argv[2:]))
    sys.exit(main(sys.argv[1:]))
