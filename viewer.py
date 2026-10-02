"""The viewer window: renders one markdown file with the dark theme.

Read mode shows the rendered document.  Edit mode (Ctrl+E) opens a plain-text
pane next to it with the markdown source: nothing is converted back and forth,
the source is just what is on disk.  "Конвертировать" re-renders the preview
from the edited text, "Сохранить" writes the file and refreshes the preview.
"""

from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from renderer import render
from theme import EDIT_SPLIT, EDIT_WIDTH, TEXT_MUTED, content_css, side_margin
from version import APP_NAME, APP_VERSION

def read_document(path: Path) -> tuple[str, str, str]:
    """Read a markdown file, guessing the encoding (never fails on content).

    Returns the text plus the encoding and the newline style, so saving the
    file back does not silently rewrite cp1251 as utf-8, add a BOM to a file
    that had none, or convert LF to CRLF.
    """
    raw = path.read_bytes()
    newline = _newline_of(raw)
    for encoding in _candidate_encodings(raw):
        try:
            return raw.decode(encoding), encoding, newline
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8", newline


def _candidate_encodings(raw: bytes) -> tuple[str, ...]:
    """A BOM is evidence, and so are NUL bytes: utf-16 happily decodes *any*
    even-length byte string, so trying it too early turns cp1251 files into
    mojibake instead of failing over to the right codec."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return ("utf-8-sig", "utf-8", "cp1251")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return ("utf-16", "utf-8", "cp1251")
    if b"\x00" in raw:
        return ("utf-16", "utf-8", "cp1251")
    return ("utf-8", "cp1251")


def _newline_of(raw: bytes) -> str:
    return "\r\n" if b"\r\n" in raw else "\n"


def _normalized(text: str) -> str:
    """Qt text widgets hand back LF-only text, so compare like for like."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


class Viewer(QMainWindow):
    def __init__(self, path: Path, settings: QSettings | None = None):
        super().__init__()
        self._path = Path(path).resolve()
        self._settings = settings or QSettings()
        self._side = -1
        self._zoom = 0

        self._text = ""          # what is on disk
        self._encoding = "utf-8"
        self._newline = "\n"
        self._rendered = ""      # what the preview currently shows (LF-normalized)
        self._baseline = ""      # editor contents matching what is on disk
        self._edit_mode = False
        self._editor: QPlainTextEdit | None = None
        self._panel: QWidget | None = None
        self._convert_btn: QPushButton | None = None
        self._save_btn: QPushButton | None = None
        self._view_geometry = None

        self.setWindowTitle(self._title(False))
        self.resize(1000, 800)

        self._browser = QTextBrowser()
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._browser.setOpenExternalLinks(True)
        self._browser.anchorClicked.connect(self._open_link)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._browser)
        self._splitter.setStretchFactor(0, 1)
        # the divider changes the preview width without resizing the window, so
        # no resizeEvent arrives and the insets would keep their old value
        self._splitter.splitterMoved.connect(lambda *_: self._set_margins())
        self.setCentralWidget(self._splitter)

        self._build_find_bar()
        self._build_edit_button()
        self._build_shortcuts()

        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self.load()
        QTimer.singleShot(0, self._set_margins)

    # -- content ----------------------------------------------------------

    def load(self) -> None:
        if not self._confirm_discard():
            return
        self._text, self._encoding, self._newline = read_document(self._path)
        self._render(self._text)
        self._baseline = _normalized(self._text)
        if self._editor is not None:
            self._editor.setPlainText(self._text)
            self._editor.document().setModified(False)
        self._update_state()

    def _render(self, text: str) -> None:
        """Re-render the preview. Called only on load/convert/save, never on
        resize (that would cost hundreds of milliseconds per step)."""
        self._rendered = _normalized(text)
        # the stylesheet has to be in place before the HTML: Qt matches CSS while
        # importing, not afterwards
        self._browser.document().setDefaultStyleSheet(content_css())
        self._browser.setHtml(render(text, self._path.parent))
        self._set_margins()
        self._browser.verticalScrollBar().setValue(0)

    def _render_busy(self, text: str) -> None:
        """Same, with a wait cursor: a big file takes ~0.5 s to render."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._render(text)
        finally:
            QApplication.restoreOverrideCursor()

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
        if not self._confirm_discard():
            event.ignore()
            return
        # Closing in edit mode must not remember the widened split window,
        # otherwise the next start looks wide but has no editor.
        if self._editing() and self._view_geometry is not None:
            self._settings.setValue("geometry", self._view_geometry)
        else:
            self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("last_dir", str(self._path.parent))
        super().closeEvent(event)

    def _open_link(self, url: QUrl) -> None:
        if url.isRelative():
            return
        QDesktopServices.openUrl(url)

    # -- edit mode --------------------------------------------------------

    def _build_edit_button(self) -> None:
        self._edit_btn = QPushButton("Редактировать")
        self._edit_btn.setToolTip("Редактировать Markdown как обычный текст (Ctrl+E)")
        self._edit_btn.clicked.connect(self._toggle_edit)
        self._edit_hint = QLabel("Ctrl+E")
        self._edit_hint.setToolTip("Правка в соседней панели: исходник слева, предпросмотр справа")
        for widget in (self._edit_btn, self._edit_hint):
            self.statusBar().addWidget(widget)
        self.statusBar().addPermanentWidget(QLabel(f"{APP_NAME} {APP_VERSION}"))

    def _title(self, unsaved: bool) -> str:
        """The version is in the title on purpose: it tells builds apart when
        an old copy is still installed somewhere else."""
        return f"{self._path.name}{' •' if unsaved else ''} — {APP_NAME} {APP_VERSION}"

    def _build_editor(self) -> None:
        panel = QWidget()
        panel.setObjectName("editorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 12, 8)
        header.setSpacing(8)
        caption = QLabel("MARKDOWN")
        caption.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self._convert_btn = QPushButton("Конвертировать")
        self._convert_btn.setToolTip("Пересчитать оформление слева (Ctrl+Enter)")
        self._convert_btn.clicked.connect(self._convert)
        self._save_btn = QPushButton("Сохранить")
        self._save_btn.setObjectName("primary")
        self._save_btn.setToolTip("Записать файл на диск (Ctrl+S)")
        self._save_btn.clicked.connect(self._save)
        for widget in (caption, self._convert_btn, self._save_btn):
            header.addWidget(widget)
        header.addStretch(1)
        header.addWidget(QLabel("Esc — выйти"))
        layout.addLayout(header)

        self._editor = QPlainTextEdit()
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setPlaceholderText("Исходный Markdown — как в блокноте")
        self._editor.textChanged.connect(self._update_state)
        layout.addWidget(self._editor, 1)

        self._panel = panel
        self._splitter.addWidget(panel)
        self._splitter.setStretchFactor(1, 1)
        panel.setVisible(False)  # addWidget reveals the child; edit mode owns it

    def _toggle_edit(self) -> None:
        if self._editing():
            self._leave_edit()
        else:
            self._enter_edit()

    def _editing(self) -> bool:
        return self._edit_mode

    def _enter_edit(self) -> None:
        if self._editor is None:
            self._build_editor()
        self._edit_mode = True
        self._editor.setPlainText(self._text)
        self._editor.document().setModified(False)
        self._baseline = _normalized(self._text)
        self._panel.setVisible(True)

        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry().width() if screen else EDIT_WIDTH
        target = min(EDIT_WIDTH, int(available * 0.92))
        if self.width() < target:
            # QByteArray, not QRect: this is what restoreGeometry expects
            self._view_geometry = self.saveGeometry()
            self.resize(target, self.height())
        self._apply_split()
        QTimer.singleShot(0, self._apply_split)  # once the layout knows the child

        self._edit_btn.setText("Выйти из правки")
        self._editor.setFocus()
        self._editor.moveCursor(QTextCursor.MoveOperation.End)
        self._update_state()

    def _leave_edit(self) -> None:
        if not self._confirm_discard():
            return
        self._edit_mode = False
        self._panel.setVisible(False)
        self._splitter.setSizes([self.width(), 0])
        QTimer.singleShot(0, self._set_margins)
        if _normalized(self._text) != self._rendered:
            # the preview showed a converted but unsaved draft: go back to disk
            self._render(self._text)
            self._baseline = _normalized(self._text)
        if self._view_geometry is not None:
            self.restoreGeometry(self._view_geometry)
            self._view_geometry = None
        self._edit_btn.setText("Редактировать")
        self._browser.setFocus()
        self._update_state()

    def _apply_split(self) -> None:
        """Preview gets EDIT_SPLIT of the window, the editor gets the rest."""
        if not self._edit_mode:
            return
        width = self.width()
        self._splitter.setSizes([int(width * EDIT_SPLIT), width - int(width * EDIT_SPLIT)])
        # the columns are only resized once the layout settles
        QTimer.singleShot(0, self._set_margins)

    def _convert(self) -> None:
        if not self._editing():
            return
        text = self._editor.toPlainText()
        if text == self._rendered:
            return
        self._render_busy(text)
        self._update_state()

    def _save(self) -> bool:
        if not self._editing():
            return False
        text = self._editor.toPlainText()
        try:
            self._path.write_text(text, encoding=self._encoding, newline=self._newline)
        except OSError as error:
            QMessageBox.critical(self, APP_NAME, f"Не удалось сохранить файл:\n{error}")
            return False
        self._text = text
        self._baseline = _normalized(text)
        self._editor.document().setModified(False)
        self._render_busy(text)  # a saved file should show what it will look like
        self._update_state()
        self.statusBar().showMessage(f"Сохранено: {self._path.name}", 3000)
        return True

    def _confirm_discard(self) -> bool:
        """False when the user cancelled and the caller must abort."""
        if not self._editing() or self._editor.toPlainText() == self._baseline:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Сохранить изменения в {self._path.name}?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save()
        return True

    def _update_state(self) -> None:
        """Title bullet, and which of the two buttons make sense right now."""
        editing = self._editing()
        text = self._editor.toPlainText() if editing else self._text
        unsaved = editing and text != self._baseline
        self.setWindowTitle(self._title(unsaved))
        self._edit_hint.setVisible(not editing)
        if self._convert_btn is not None:
            self._convert_btn.setEnabled(editing and text != self._rendered)
        if self._save_btn is not None:
            self._save_btn.setEnabled(unsaved)

    def _handle_esc(self) -> None:
        if self._find_bar.isVisible():
            self._close_find()
        elif self._editing():
            self._leave_edit()

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
            "Esc": self._handle_esc,
            "Ctrl+F": self._toggle_find,
            "F3": self._find_next,
            "Shift+F3": lambda: self._find_next(backward=True),
            "F5": self.load,
            "Ctrl+R": self.load,
            "Ctrl+O": self._open_file,
            "Ctrl+E": self._toggle_edit,
            "Ctrl+Return": self._convert,
            "Ctrl+S": self._save,
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
        if not self._confirm_discard():
            return
        start = self._settings.value("last_dir", str(Path.home()))
        name, _ = QFileDialog.getOpenFileName(
            self, APP_NAME, start, "Markdown (*.md *.markdown *.txt);;All files (*)"
        )
        if not name:
            return
        self._path = Path(name).resolve()
        self.setWindowTitle(self._title(False))
        self.load()