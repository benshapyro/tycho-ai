"""Pictures of a game window for the brain eval's look cases (#548), drawn here.

They are our own drawings, never game art from elsewhere: a few shapes and HUD-style
words, enough to tell whether an answer came from the picture. Each is drawn at the
size of a 1080p game window and made small with ``tycho.eyes.shrink``, as a real look
is, so its cost and time match a real one's. A picture the PC captures can take one's
place later, under the same name.
"""

from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFont

from tycho.eyes import Picture, shrink

SIZE = (1920, 1080)
"""A 1080p game window: ``shrink`` makes it 1429 x 804, as it would a real one."""


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size)


def _planet() -> Image.Image:
    """A pilot's view: a ringed orange planet named on the HUD, and the HUD's own
    numbers (fuel 23%, speed 0), on a starfield."""
    image = Image.new("RGB", SIZE, (6, 8, 18))
    draw = ImageDraw.Draw(image)
    for n in range(240):  # stars, the same every time
        x, y = (n * 7919) % SIZE[0], (n * 104_729) % SIZE[1]
        draw.point((x, y), fill=(200, 200, 220))
    draw.ellipse((560, 180, 1360, 980), fill=(196, 112, 48))
    draw.ellipse((360, 520, 1560, 640), outline=(230, 200, 160), width=10)
    hud = (90, 230, 200)
    draw.rectangle((880, 110, 1040, 150), outline=hud, width=3)
    draw.text((960, 130), "HURSTON", fill=hud, font=_font(30), anchor="mm")
    draw.text((960, 990), "SCM   0 m/s", fill=hud, font=_font(40), anchor="mm")
    draw.text((80, 900), "FUEL 23%", fill=hud, font=_font(44))
    draw.text((80, 960), "QT FUEL 61%", fill=hud, font=_font(44))
    return image


PICTURES: dict[str, Callable[[], Image.Image]] = {"planet": _planet}
"""Each picture a case can name with ``picture``."""


def picture(name: str) -> Picture:
    """The named picture as the brain gets it, or the run stops (a misspelt name)."""
    if name not in PICTURES:
        raise SystemExit(f"no picture called {name!r}: there are {sorted(PICTURES)}")
    return shrink(PICTURES[name]())
