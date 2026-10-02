"""The viewer window: renders one markdown file with the dark theme."""

from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QTextBrowser,
    QToolButton,
    QWidget,
)

from renderer import render
from theme import TEXT_MUTED, content_css, side_margin
from version import APP_NAME

_ENCODINGS = ("utf-8-sig", "utf-16", "cp1251")


def read_document(path: Path) -> str:
    """Read a markdown file, guessing the encoding (never fails on content)."""
    raw = path.read_bytes()
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class Viewer(QMainWindow):
    def __init__(self, path: Path, settings: QSettings | None = None):
        super().__init__()
        self._path = Path(path).resolve()
        self._settings = settings or QSettings()
        self._side = -1
        self._zoom = 0

        self.setWindowTitle(self._path.name)
        self.resize(1000, 800)

        self._browser = QTextBrowser()
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._browser.setOpenExternalLinks(True)
        self._browser.anchorClicked.connect(self._open_link)
        self.setCentralWidget(self._browser)

        self._build_find_bar()
        self._build_shortcuts()

        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self.load()
        QTimer.singleShot(0, self._set_margins)

    # -- content ----------------------------------------------------------

    def load(self) -> None:
        self._html = render(read_document(self._path), self._path.parent)
        # the stylesheet has to be in place before the HTML: Qt matches CSS while
        # importing, not afterwards
        self._browser.document().setDefaultStyleSheet(content_css())
        self._browser.setHtml(self._html)
        self._set_margins()
        self._browser.verticalScrollBar().setValue(0)

    def _set_margins(self) -> None:
        """Keep the text column at a readable width.

        Done with viewport margins rather than CSS: a resize then costs a
        geometry update instead of re-parsing the whole document.
        """
        side = side_margin(self._browser.contentsRect().width())
        if side != self._side:
            self._side = side
            self._browser.setViewportMargins(side, 0, side, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_margins()

    def closeEvent(self, event):
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("last_dir", str(self._path.parent))
        super().closeEvent(event)

    def _open_link(self, url: QUrl) -> None:
        if url.isRelative():
            return
        QDesktopServices.openUrl(url)

    # -- find bar ---------------------------------------------------------

    def _build_find_bar(self) -> None:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(6)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Find in document")
        self._find_edit.setFixedWidth(240)
        self._find_edit.returnPressed.connect(self._find_next)
        self._find_edit.textChanged.connect(self._update_hits)

        self._find_status = QLabel("")
        self._find_status.setStyleSheet(f"color: {TEXT_MUTED};")

        prev_btn = QToolButton()
        prev_btn.setText("↑")
        prev_btn.setToolTip("Previous (Shift+F3)")
        prev_btn.clicked.connect(lambda: self._find_next(backward=True))

        next_btn = QToolButton()
        next_btn.setText("↓")
        next_btn.setToolTip("Next (F3)")
        next_btn.clicked.connect(self._find_next)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self._close_find)

        for widget in (prev_btn, self._find_edit, next_btn, self._find_status, close_btn):
            layout.addWidget(widget)

        self._find_bar = bar
        bar.setVisible(False)
        self.statusBar().addPermanentWidget(bar)

    def _toggle_find(self) -> None:
        show = not self._find_bar.isVisible()
        self._find_bar.setVisible(show)
        if show:
            self._find_edit.setFocus()
            self._find_edit.selectAll()
            self._update_hits(self._find_edit.text())
        else:
            self._browser.setFocus()

    def _close_find(self) -> None:
        self._find_bar.setVisible(False)
        self._browser.setFocus()

    def _update_hits(self, text: str) -> None:
        if not text:
            self._find_status.setText("")
            return
        hits = self._browser.document().toPlainText().lower().count(text.lower())
        self._find_status.setText(str(hits))

    def _find_next(self, backward: bool = False) -> None:
        needle = self._find_edit.text()
        if not needle or self._browser.find(needle, backward):
            return
        # wrap around
        cursor = self._browser.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start
        )
        self._browser.setTextCursor(cursor)
        self._browser.find(needle, backward)

    # -- shortcuts --------------------------------------------------------

    def _build_shortcuts(self) -> None:
        actions = {
            "Ctrl+W": self.close,
            "Ctrl+Q": self.close,
            "Esc": self._close_find,
            "Ctrl+F": self._toggle_find,
            "F3": self._find_next,
            "Shift+F3": lambda: self._find_next(backward=True),
            "F5": self.load,
            "Ctrl+R": self.load,
            "Ctrl+O": self._open_file,
            "Ctrl+=": lambda: self._set_zoom(self._zoom + 1),
            "Ctrl++": lambda: self._set_zoom(self._zoom + 1),
            "Ctrl+-": lambda: self._set_zoom(self._zoom - 1),
            "Ctrl+0": lambda: self._set_zoom(0),
        }
        for keys, slot in actions.items():
            QShortcut(QKeySequence(keys), self, slot)

    def _set_zoom(self, level: int) -> None:
        delta = level - self._zoom
        for _ in range(abs(delta)):
            self._browser.zoomIn(1 if delta > 0 else -1)
        self._zoom = level

    def _open_file(self) -> None:
        start = self._settings.value("last_dir", str(Path.home()))
        name, _ = QFileDialog.getOpenFileName(
            self, APP_NAME, start, "Markdown (*.md *.markdown *.txt);;All files (*)"
        )
        if not name:
            return
        self._path = Path(name).resolve()
        self.setWindowTitle(self._path.name)
        self.load()