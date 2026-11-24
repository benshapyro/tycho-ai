"""Reading the words on one frame with Windows' own text recognition (Windows.Media.Ocr,
#588). On the PC, in the capture's child process (``eyes.child``): nothing is sent
anywhere, and the frame is dropped as soon as it's read (ADR 14).

The PC test (24 Nov 2026, 2560x1440) read the star map's "SEARCH LOCATIONS..." box in
about 150 ms. Windows reads frames up to ``OcrEngine.max_image_dimension`` on a side, so
a bigger one (4K) is shrunk first and the boxes scaled back to the frame's pixels.

Nothing here is reached on import: the WinRT modules load on the first reading.
"""

from typing import TYPE_CHECKING

from tycho.eyes import CaptureError
from tycho.eyes.words import MAX_WORDS, Reading, Word

if TYPE_CHECKING:
    from PIL import Image

LANGUAGE = "en-US"
"""The star map's words are English: the game has no other language yet."""
SECONDS = 3.0


def read_words(image: Image.Image) -> Reading:
    """The words on ``image`` (the whole frame), with their boxes in its pixels."""
    from PIL import Image
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_language(Language(LANGUAGE))
    if engine is None:
        raise CaptureError("Windows has no English text recognition installed")
    width, height = image.size
    largest = int(OcrEngine.max_image_dimension)
    scale = min(1.0, largest / max(width, height))
    small = image.convert("RGBA")
    if scale < 1.0:
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        small = small.resize(size, Image.Resampling.BILINEAR)
    bgra = small.tobytes("raw", "BGRA")
    try:
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            bgra, BitmapPixelFormat.BGRA8, small.width, small.height
        )
        reading = engine.recognize_async(bitmap)
        reading.wait(SECONDS)
        result = reading.get_results()
    except OSError as e:
        raise CaptureError(f"Windows couldn't read the game window ({e})") from e
    words: list[Word] = []
    for number, line in enumerate(result.lines):
        for word in line.words:
            box = word.bounding_rect
            words.append(
                Word(
                    word.text,
                    round(box.x / scale),
                    round(box.y / scale),
                    round(box.width / scale),
                    round(box.height / scale),
                    number,
                )
            )
            if len(words) >= MAX_WORDS:
                return Reading(width, height, tuple(words))
    return Reading(width, height, tuple(words))
