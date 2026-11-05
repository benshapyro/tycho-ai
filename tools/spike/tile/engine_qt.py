# /// script
# requires-python = ">=3.12"
# dependencies = ["PySide6>=6.8"]
# ///
"""The Tile test window drawn with Qt WebEngine: the real card HTML, truly see-through.

Its own environment (PEP 723), so PySide6 is never added to Tycho itself. uv downloads
it once into its cache (about 400 MB). Started by tile_window.py; prints the window
handle, then runs.
"""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).parent))
import tile_win32 as win32

W, H = 420, 260


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--corner", choices=["tr", "tl", "br", "bl"], default="tr")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.setWindowFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
        | Qt.WindowType.WindowTransparentForInput
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )
    view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    view.page().setBackgroundColor(QColor(0, 0, 0, 0))
    view.load(QUrl.fromLocalFile(str(Path(__file__).with_name("card.html"))))

    screen = QGuiApplication.primaryScreen().availableGeometry()
    m = 40
    x = screen.right() - W - m if args.corner[1] == "r" else screen.left() + m
    y = screen.top() + m if args.corner[0] == "t" else screen.bottom() - H - m
    view.setGeometry(x, y, W, H)
    view.show()

    hwnd = int(view.winId())
    win32.make_tile(hwnd)
    print(f"HWND {hwnd}", flush=True)

    top = QTimer()
    top.timeout.connect(lambda: win32.raise_to_top(hwnd))
    top.start(2000)
    QTimer.singleShot(int(args.seconds * 1000), app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
