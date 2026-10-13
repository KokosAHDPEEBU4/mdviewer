"""Draws the tab close-button images into assets/.

QSS cannot move the native close-button widget (`position`, `margin` and
`subcontrol-origin` are all ignored for `QTabBar::close-button`, and the
widget keeps its native 16x16 size), so the right-edge gap is baked into the
image: the glyph square ends at x=11 of the 16px canvas, leaving 5
transparent pixels (+1px of tab border) between the ✕ and the tab's right
wall. Four variants: dark/light x normal/hover.

Run from the project root:
    .venv\\Scripts\\python tools\\make_close_icons.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

from theme import palette

SIZE = 16
GLYPH = QRectF(1, 3, 10, 10)   # the ✕ square; x=11..16 stays transparent (the gap)
BG = QRectF(0, 2, 13, 12)      # hover backing plate
ASSETS = Path(__file__).resolve().parent.parent / "assets"

VARIANTS = {
    "close_tab_dark.png":        ("dark", False),
    "close_tab_dark_hover.png":  ("dark", True),
    "close_tab_light.png":       ("light", False),
    "close_tab_light_hover.png": ("light", True),
}


def draw(path: Path, theme: str, hover: bool) -> None:
    p = palette(theme)
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if hover:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p["SURFACE_2"]))
        painter.drawRoundedRect(BG, 4, 4)
        pen_colour = p["TEXT_HEAD"] if theme == "dark" else p["TEXT"]
    else:
        pen_colour = p["TEXT_MUTED"]
    inset = 1.0
    pen = QPen(QColor(pen_colour))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    square = GLYPH.adjusted(inset, inset, -inset, -inset)
    painter.drawLine(square.topLeft(), square.bottomRight())
    painter.drawLine(square.topRight(), square.bottomLeft())
    painter.end()
    pm.save(str(path))


def main() -> int:
    _app = QApplication(sys.argv[:1])  # QPixmap needs a QGuiApplication
    ASSETS.mkdir(exist_ok=True)
    for name, (theme, hover) in VARIANTS.items():
        draw(ASSETS / name, theme, hover)
        print(f"ok  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
