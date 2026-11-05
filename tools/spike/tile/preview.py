"""Draw every Tile card to PNGs, to look at without the game (ADR 16).

Each card is drawn as ``tycho.tile.draw`` would put it on screen, over a busy dark
background like a cockpit, so the see-through glass can be judged. The examples are
made up, like the mockup's.

Usage: uv run python tools/spike/tile/preview.py [out_dir] [--accent "#f5901e"] [--scale 1.2]
"""

import argparse
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

from tycho.tile import Board, Card, Row
from tycho.tile.cards import confirm, long_answer, notice
from tycho.tile.design import TYCHO_ACCENT
from tycho.tile.draw import Look, draw

EXAMPLES: dict[str, Card] = {
    "list": Card(
        "list",
        title="Best places to sell",
        label="Sell · Laranite",
        source="UEX",
        rows=(
            Row("Everus Harbor", "Hurston orbit", "3,048"),
            Row("Area18 TDD", "ArcCorp", "3,012"),
            Row("Port Tressler", "microTech orbit", "2,987"),
        ),
    ),
    "route": Card(
        "route",
        title="Medical supplies",
        label="Route · 96 SCU",
        source="UEX",
        rows=(Row("Lorville", "Buy", "1,812 / SCU"), Row("Area18", "Sell", "2,455 / SCU")),
        hero=("Profit, full load", "61,728"),
    ),
    "recipe": Card(
        "recipe",
        title="FS-9 LMG",
        label="Craft",
        source="Star Citizen Wiki",
        rows=(
            Row("Lindinium", value="0.06 SCU"),
            Row("Hephaestanite", value="0.04 SCU"),
            Row("Iron", value="0.03 SCU"),
        ),
    ),
    "ship": Card(
        "ship",
        title="Corsair",
        label="Exploration gunship",
        source="Game files",
        rows=(
            Row("Cargo", value="72 SCU"),
            Row("Crew", value="1 to 4"),
            Row("Size", value="Large"),
            Row("Quantum range", value="681.8 Gm"),
        ),
    ),
    "fleet": Card(
        "fleet",
        title="Best for mining",
        label="Your fleet · Mining",
        rows=(
            Row("Prospector", "Built for it: mining laser, 32 SCU ore", "Mines"),
            Row("Cutlass Black", "Can't mine; can carry ore", "Hauls", dim=True),
        ),
    ),
    "steps": Card(
        "steps",
        title="Refuel",
        label="How to",
        rows=(
            Row("Land on a pad at a station or outpost with services."),
            Row("Open the pad's vehicle services."),
            Row("Choose hydrogen, quantum, or both, and fill."),
            Row("Pay and take off."),
        ),
    ),
    "headlines": Card(
        "headlines",
        title="Next patch",
        label="News",
        source="RSI",
        rows=(
            Row("PTU wave 2 opens to more backers", value="03 Nov"),
            Row("New cargo hauling missions", value="30 Oct"),
            Row("Mining balance pass", value="28 Oct"),
        ),
    ),
    "warning": Card(
        "warning",
        title="Lock all doors",
        label="No key bound",
        body=('Keybindings, search "lock all doors", give it a key. Then say it again.',),
    ),
    "notice": notice(
        "Speech recognition moved to the processor: the graphics card was full. "
        "Replies may be a little slower."
    ),
    "long": long_answer(
        "Quantum travel burns quantum fuel, a separate tank from hydrogen. A jump uses fuel "
        "in proportion to the distance, and your quantum drive's efficiency sets how much "
        "per million kilometres. Before a long route, check the route length against your "
        "tank. Refuel at any station's refuel menu once you've landed or docked."
    ),
    "confirm": confirm("Eject? Say confirm, Ace.", 5.0),
}


def cockpit(w: int, h: int) -> Image.Image:
    """A busy dark background: gradients, lines and a few bright marks."""
    rng = random.Random(7)  # noqa: S311 - the same picture each time, not a secret
    bg = Image.new("RGBA", (w, h), (6, 9, 14, 255))
    d = ImageDraw.Draw(bg)
    for y in range(h):
        v = int(10 + 22 * (y / h))
        d.line((0, y, w, y), fill=(v, v + 4, v + 12, 255))
    for _ in range(40):
        x, y = rng.randrange(w), rng.randrange(h)
        d.line(
            (x, y, x + rng.randrange(-200, 200), y + rng.randrange(-80, 80)),
            fill=(90, 140, 170, 120),
            width=2,
        )
    for _ in range(25):
        x, y = rng.randrange(w), rng.randrange(h)
        d.text((x, y), f"{rng.randrange(100, 9999)}", fill=(160, 200, 230, 160))
    for i in range(6):
        r = 60 + 30 * i
        d.arc(
            (w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r), 200, 340, fill=(240, 160, 60, 90), width=2
        )
    d.ellipse((w * 0.7, h * 0.1, w * 0.7 + 120, h * 0.1 + 120), fill=(230, 220, 200, 140))
    _ = math
    return bg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path, nargs="?", default=Path("recordings/tile-preview"))
    parser.add_argument("--accent", default=TYCHO_ACCENT)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--see-through", type=int, default=20)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    look = Look(accent=args.accent, scale=args.scale, see_through=args.see_through)

    sheet_parts: list[Image.Image] = []
    for name, card in EXAMPLES.items():
        clock = [100.0]
        board = Board(clock=lambda c=clock: c[0])
        board.show(card)
        clock[0] += 2.0  # part way through its time
        light = "thinking" if name == "list" else "listening" if name == "steps" else None
        tile = draw(board.view(), look, now=clock[0], light=light, phase=0.3)
        bg = cockpit(tile.width + 60, tile.height + 60)
        bg.alpha_composite(tile, (30, 30))
        bg.convert("RGB").save(args.out / f"{name}.png")
        sheet_parts.append(bg)

    # A pinned card that gave way, as a strip under the newer one.
    clock = [100.0]
    board = Board(clock=lambda: clock[0])
    board.show(EXAMPLES["list"], pinned=True)
    board.show(EXAMPLES["route"])
    tile = draw(board.view(), look, now=clock[0])
    bg = cockpit(tile.width + 60, tile.height + 60)
    bg.alpha_composite(tile, (30, 30))
    bg.convert("RGB").save(args.out / "strip.png")
    sheet_parts.append(bg)

    cols = 4
    cell_w = max(p.width for p in sheet_parts)
    rows = math.ceil(len(sheet_parts) / cols)
    row_h = [max(p.height for p in sheet_parts[r * cols : (r + 1) * cols]) for r in range(rows)]
    sheet = Image.new("RGB", (cell_w * cols, sum(row_h)), (0, 0, 0))
    y = 0
    for r in range(rows):
        for c, part in enumerate(sheet_parts[r * cols : (r + 1) * cols]):
            sheet.paste(part.convert("RGB"), (c * cell_w, y))
        y += row_h[r]
    sheet.save(args.out / "all.png")
    print(f"{len(sheet_parts)} previews in {args.out}")


if __name__ == "__main__":
    main()
