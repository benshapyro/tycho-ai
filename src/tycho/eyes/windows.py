"""One frame of the game window, through the Windows Graphics Capture API (ADR 3, #31).

Only the window of the pack's program (``window`` in ``pack.toml``) is ever captured:
never the desktop or another window. The Tile is a window of its own, so it isn't in
the picture. A minimised or missing game window gives no picture, and the player is
told why. The capture runs on one worker thread of its own, in Windows' multi-threaded
apartment, and is closed as soon as one frame has arrived.

Nothing here is reached on import: the WinRT modules load on the first capture, so the
rest of Tycho (and the cloud's tests) never need them.
"""

import contextlib
import ctypes
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from typing import TYPE_CHECKING

from tycho import win32
from tycho.eyes import CaptureError, Picture, shrink
from tycho.input.windows import executable_of

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger(__name__)

FRAME_SECONDS = 1.5
"""How long to wait for Windows to hand over the first frame (usually under 100 ms)."""
SMALLEST = 200
"""A window narrower or shorter than this is a launcher or a splash, not the game."""


class WindowsCamera:
    """Takes one picture of ``executable``'s largest visible window. Satisfies
    ``runtime.Camera``. Blocking: the session calls it off the event loop."""

    def __init__(self, executable: str) -> None:
        self.executable = executable
        self._worker = ThreadPoolExecutor(1, "tycho-eyes", initializer=_multi_threaded)

    def take(self) -> Picture:
        window = game_window(self.executable)
        if window is None:
            raise CaptureError(f"no {self.executable} window is open (or it's minimised)")
        return shrink(self._worker.submit(_frame, window).result())

    def close(self) -> None:
        self._worker.shutdown(wait=False, cancel_futures=True)


def game_window(executable: str) -> int | None:
    """The handle of ``executable``'s largest visible, not minimised, top-level window."""
    user32 = win32.user32()
    found: list[tuple[int, int]] = []  # (area, handle)
    wanted = executable.lower()

    def each(window: int, _: int) -> bool:
        if user32.IsWindowVisible(window) and not user32.IsIconic(window):
            rect = wintypes.RECT()
            if user32.GetWindowRect(window, ctypes.byref(rect)):
                width, height = rect.right - rect.left, rect.bottom - rect.top
                if min(width, height) >= SMALLEST and executable_of(window).lower() == wanted:
                    found.append((width * height, window))
        return True  # keep going: the game can own more than one window

    user32.EnumWindows(win32.WNDENUMPROC(each), 0)
    return max(found)[1] if found else None


def _multi_threaded() -> None:
    """The worker joins Windows' multi-threaded apartment, which a free-threaded frame
    pool needs. Already in one (or another), WinRT says so, and it carries on."""
    from winrt.runtime import ApartmentType, init_apartment

    with contextlib.suppress(OSError):
        init_apartment(ApartmentType.MULTI_THREADED)


def _frame(window: int) -> Image.Image:
    """One frame of ``window``, without the mouse pointer. On the worker thread."""
    from PIL import Image
    from winrt.windows.graphics.capture import Direct3D11CaptureFramePool
    from winrt.windows.graphics.capture.interop import create_for_window
    from winrt.windows.graphics.directx import DirectXPixelFormat
    from winrt.windows.graphics.directx.direct3d11.interop import create_direct3d11_device
    from winrt.windows.graphics.imaging import SoftwareBitmap
    from winrt.windows.storage.streams import Buffer

    try:
        item = create_for_window(window)
        device = create_direct3d11_device()
        bgra = DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED
        with (
            Direct3D11CaptureFramePool.create_free_threaded(device, bgra, 1, item.size) as pool,
            pool.create_capture_session(item) as session,
        ):
            session.is_cursor_capture_enabled = False
            with contextlib.suppress(OSError):  # Windows 10 always draws the yellow border
                session.is_border_required = False
            session.start_capture()
            deadline = time.monotonic() + FRAME_SECONDS
            while (frame := pool.try_get_next_frame()) is None:
                if time.monotonic() > deadline:
                    raise CaptureError("Windows sent no picture of the game window")
                time.sleep(0.01)
            with frame:
                copying = SoftwareBitmap.create_copy_from_surface_async(frame.surface)
                copying.wait(FRAME_SECONDS)
                bitmap = copying.get_results()
        with bitmap:
            width, height = bitmap.pixel_width, bitmap.pixel_height
            buffer = Buffer(width * height * 4)
            bitmap.copy_to_buffer(buffer)
            pixels = bytes(memoryview(buffer))
    except OSError as e:  # WinRT's errors: the window closed, or capture isn't allowed
        log.warning("Couldn't capture the game window: %s", e)
        raise CaptureError(f"Windows couldn't capture the game window ({e})") from e
    return Image.frombuffer("RGBA", (width, height), pixels, "raw", "BGRA", 0, 1)
