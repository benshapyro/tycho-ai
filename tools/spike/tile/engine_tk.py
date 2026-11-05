"""The Tile test window drawn with tkinter (in Python already; no extra download).

tkinter can only make one exact colour see-through, so the card has hard edges and a
solid background. That's enough to test the window's behaviour; the Qt engine tests
the real look. Started by tile_window.py; prints the window handle, then runs.
"""

import argparse
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tile_win32 as win32

KEY = "#010203"  # the colour Windows makes see-through
GLASS, LINE, TEXT, DIM = "#0e1522", "#f5901e", "#e4ebf5", "#8c99ae"
CARDS = [
    ("SELL · LARANITE", "Best places to sell", ["Everus Harbor   3,048", "Area18 TDD      3,012"]),
    ("ROUTE · 96 SCU", "Medical supplies", ["Lorville → Area18", "Profit       61,728"]),
    ("NO KEY BOUND", "Lock all doors", ["Keybindings: search", '"lock all doors"']),
]
W, H, CUT = 380, 150, 12


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--corner", choices=["tr", "tl", "br", "bl"], default="tr")
    args = parser.parse_args()

    previous = win32.foreground()  # before any UI exists: tkinter's hidden window takes it
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.wm_attributes(topmost=True, transparentcolor=KEY)  # pyright: ignore[reportUnknownMemberType] - one stub overload is untyped
    root.configure(bg=KEY)
    sw, sh, m = root.winfo_screenwidth(), root.winfo_screenheight(), 40
    x = sw - W - m if args.corner[1] == "r" else m
    y = m if args.corner[0] == "t" else sh - H - m
    root.geometry(f"{W}x{H}+{x}+{y}")
    canvas = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0)
    canvas.pack()

    def draw(i: int) -> None:
        kicker, title, rows = CARDS[i % len(CARDS)]
        canvas.delete("all")
        shape = [CUT, 0, W - 1, 0, W - 1, H - CUT, W - CUT, H - 1, 0, H - 1, 0, CUT]
        canvas.create_polygon(shape, fill=GLASS, outline=LINE)
        canvas.create_text(
            18,
            16,
            text="✦ " + kicker,
            anchor="nw",
            fill=LINE,
            font=("Bahnschrift SemiCondensed", 10, "bold"),
        )
        canvas.create_text(
            18,
            38,
            text=title.upper(),
            anchor="nw",
            fill=TEXT,
            font=("Bahnschrift SemiCondensed", 15, "bold"),
        )
        for n, row in enumerate(rows):
            canvas.create_text(
                18,
                72 + n * 26,
                text=row,
                anchor="nw",
                fill=TEXT if n == 0 else DIM,
                font=("Cascadia Mono", 11),
            )
        root.after(4000, draw, i + 1)

    root.update_idletasks()
    hwnd = int(root.wm_frame(), 16)
    win32.make_tile(hwnd)  # styles first, while hidden
    root.deiconify()  # tkinter must show it itself, or it never draws the canvas
    root.update()
    gave_back = win32.give_back_focus(previous)
    print(f"HWND {hwnd}", flush=True)
    print(f"FOCUS {'given back' if gave_back else 'never taken'}", flush=True)
    draw(0)

    def keep_on_top() -> None:
        win32.raise_to_top(hwnd)
        root.after(2000, keep_on_top)

    keep_on_top()
    root.after(int(args.seconds * 1000), root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
