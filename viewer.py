"""The viewer window: renders one markdown file with the dark theme.

Read mode shows the rendered document.  Edit mode (Ctrl+E) opens a plain-text
pane next to it with the markdown source: nothing is converted back and forth,
the source is just what is on disk.  "Конвертировать" re-renders the preview
from the edited text, "Сохранить" writes the file and refreshes the preview.
"""

from pathlib import Path
import ctypes
import re
import sys

from PyQt6.QtCore import QRegularExpression, QSettings, QTimer, QUrl, Qt
from PyQt6.QtGui import (
    QDesktopServices,
    QFont,
    QGuiApplication,
    QKeySequence,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

from renderer import Heading, render_with_outline
from theme import (
    EDIT_SPLIT,
    EDIT_WIDTH,
    MIN_COLUMN,
    SURFACE,
    TEXT_MUTED,
    TOC_WIDTH,
    content_css,
    side_margin,
)
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


# DwmSetWindowAttribute: the native caption is drawn light grey on Windows 10
# unless the app opts out, and Qt exposes no API for it.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20          # Windows 10 20H1+
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19      # Windows 10 1809..1909


def enable_dark_title_bar(widget) -> bool:
    """Ask DWM for a dark caption. False when the OS will not have it."""
    if sys.platform != "win32":
        return False
    try:
        value = ctypes.c_int(1)
        for attribute in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(widget.winId()), attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result == 0:
                return True
    except (AttributeError, OSError):
        pass
    return False


class Viewer(QMainWindow):
    def __init__(self, path: Path, settings: QSettings | None = None):
        super().__init__()
        self._path = Path(path).resolve()
        self._settings = settings or QSettings()
        self._side = -1
        self._zoom = 0
        self._editor_zoom = 0
        self._zoom_label = None
        self._editor_zoom_label = None
        self._editor_base_font = None
        self._shortcuts_built = False

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
        self._dark_titled = False
        self._preview_width = 0      # preview width to preserve when editing
        self._outline: list[Heading] = []
        self._outline_offset: list[float] = []   # y of each heading in the doc
        self._outline_ready = False  # offsets are measured from the first render
        self._toc_syncing = False

        self.setWindowTitle(self._title(False))
        self.resize(1000, 800)

        self._browser = QTextBrowser()
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._browser.setOpenExternalLinks(True)
        self._browser.anchorClicked.connect(self._open_link)
        self._browser.verticalScrollBar().valueChanged.connect(self._sync_outline)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._build_outline())
        self._splitter.addWidget(self._browser)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        # the divider changes the preview width without resizing the window, so
        # no resizeEvent arrives and the insets would keep their old value
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self.setCentralWidget(self._splitter)

        self._edit_btn = None
        self._overlay = None
        self._find_bar = None

        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self.load()
        QTimer.singleShot(0, self._set_margins)
        self._build_shortcuts()
        # the hover chrome warms up QStyleSheetStyle (~300 ms); build it after
        # the first frame so the document shows up sooner
        QTimer.singleShot(0, self._ensure_overlay)

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
        html, self._outline = render_with_outline(text, self._path.parent)
        self._browser.setHtml(html)
        self._outline_ready = False
        self._populate_outline()
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

    def _on_splitter_moved(self, *_args) -> None:
        self._set_margins()
        # a wider preview re-wraps the text, so the cached heading offsets move
        self._outline_ready = False

    # -- table of contents -------------------------------------------------

    def _build_outline(self) -> QWidget:
        self._toc = QListWidget()
        self._toc.setObjectName("outline")
        self._toc.setUniformItemSizes(False)
        self._toc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._toc.itemClicked.connect(self._outline_clicked)
        self._toc.setVisible(False)  # shown once a document has headings
        return self._toc

    def _populate_outline(self) -> None:
        """Rebuild the list from the last render (cheap: it is plain text)."""
        self._toc.clear()
        for heading in self._outline:
            item = QListWidgetItem("  " * (heading.level - 1) + heading.label)
            item.setData(Qt.ItemDataRole.UserRole, heading.anchor)
            item.setToolTip(heading.label)
            self._toc.addItem(item)
        visible = self._settings.value("toc_visible", True, type=bool) and bool(self._outline)
        self._set_outline_visible(visible)

    def _set_outline_visible(self, visible: bool) -> None:
        self._toc.setVisible(visible)
        if visible:
            self._toc.setFixedWidth(int(self._settings.value("toc_width", TOC_WIDTH)))
        self._settings.setValue("toc_visible", visible)
        self._apply_split()
        QTimer.singleShot(0, self._set_margins)
        QTimer.singleShot(0, self._sync_outline)

    def _outline_width(self) -> int:
        """Width the outline pane should take in the splitter (0 when hidden)."""
        if not self._toc.isVisible():
            return 0
        return int(self._settings.value("toc_width", TOC_WIDTH))

    def _toggle_outline(self) -> None:
        self._set_outline_visible(not self._toc.isVisible())

    def _outline_clicked(self, item: QListWidgetItem) -> None:
        anchor = item.data(Qt.ItemDataRole.UserRole)
        self._browser.scrollToAnchor(anchor)
        self._sync_outline()

    def _measure_outline(self) -> None:
        """Map every heading anchor to a y offset in the document layout.

        The anchor name is a character format, not text (``<a name=...>`` has
        no visible content), so it is read from the block fragments rather than
        from ``block.text()``. Measured lazily on first need: block layout for a
        large file is not free and the outline is only useful once scrolled.
        """
        layout = self._browser.document().documentLayout()
        layout.documentSize()  # a lazy document reports every block at y=0
        offsets = [-1.0] * len(self._outline)
        wanted = {heading.anchor: index for index, heading in enumerate(self._outline)}
        block = self._browser.document().begin()
        seen = 0
        while block.isValid():
            fragment = block.begin()
            while fragment != block.end():
                piece = fragment.fragment()
                if piece.isValid():
                    for name in piece.charFormat().anchorNames():
                        if name in wanted:
                            offsets[wanted[name]] = layout.blockBoundingRect(block).top()
                            seen += 1
                fragment += 1
            if seen == len(wanted):
                break
            block = block.next()
        # A document that has not been laid out yet reports every heading at 0;
        # keep the old offsets and try again after the next scroll.
        if len(self._outline) > 1 and min(offsets) == max(offsets):
            return
        self._outline_offset = offsets
        self._outline_ready = True

    def _sync_outline(self) -> None:
        """Highlight the section that owns the top of the viewport."""
        if self._toc_syncing or not self._toc.isVisible() or not self._outline:
            return
        if not self._outline_ready:
            self._measure_outline()
        bar = self._browser.verticalScrollBar()
        scroll = bar.value()
        if scroll >= bar.maximum():
            current = len(self._outline) - 1  # scrolled to the end
        else:
            current = 0
            for index, offset in enumerate(self._outline_offset):
                if offset <= scroll + 24:
                    current = index
                else:
                    break
        if self._toc.currentRow() == current:
            return
        self._toc_syncing = True
        try:
            self._toc.setCurrentRow(current)
            self._toc.scrollToItem(self._toc.item(current), QListWidget.ScrollHint.EnsureVisible)
        finally:
            self._toc_syncing = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._dark_titled:
            self._dark_titled = True
            enable_dark_title_bar(self)
        QTimer.singleShot(0, self._apply_split)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_margins()
        self._resize_toc()
        if getattr(self, "_overlay", None) is not None and self._overlay.isVisible():
            self._place_overlay()

    def _resize_toc(self) -> None:
        """Keep the outline pane at its fixed width when the window resizes;
        the splitter would otherwise hand the extra space to pane 0."""
        if not self._toc.isVisible():
            return
        sizes = self._splitter.sizes()
        if len(sizes) < 2:
            return
        width = self._outline_width()
        if sizes[0] != width:
            sizes[0] = width
            self._splitter.setSizes(sizes)

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
        self._edit_btn.clicked.connect(self._toggle_edit)

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
        caption = QLabel("ИСХОДНИК")
        caption.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self._convert_btn = QPushButton("Конвертировать")
        self._convert_btn.clicked.connect(self._convert)
        self._save_btn = QPushButton("Сохранить")
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._save)
        for widget in (caption, self._convert_btn, self._save_btn):
            header.addWidget(widget)
        header.addStretch(1)
        # the editor has its own zoom, independent of the preview
        self._editor_zoom_out = QToolButton()
        self._editor_zoom_out.setText("−")
        self._editor_zoom_out.clicked.connect(lambda: self._set_editor_zoom(self._editor_zoom - 1))
        self._editor_zoom_label = QToolButton()
        self._editor_zoom_label.setText("100%")
        self._editor_zoom_label.clicked.connect(lambda: self._set_editor_zoom(0))
        self._editor_zoom_in = QToolButton()
        self._editor_zoom_in.setText("+")
        self._editor_zoom_in.clicked.connect(lambda: self._set_editor_zoom(self._editor_zoom + 1))
        for widget in (self._editor_zoom_out, self._editor_zoom_label, self._editor_zoom_in):
            header.addWidget(widget)
        layout.addLayout(header)

        self._editor = QPlainTextEdit()
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._editor.setPlaceholderText("Исходный Markdown — как в блокноте")
        self._editor.textChanged.connect(self._update_state)
        self._editor_base_font = self._editor.font()
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
        self._ensure_overlay()
        # measure the preview before anything is added to the splitter: adding
        # the (still hidden) editor panel already hands it space
        self._preview_width = max(self._browser.width(), MIN_COLUMN)
        if self._editor is None:
            self._build_editor()
        self._edit_mode = True
        self._editor.setPlainText(self._text)
        self._editor.document().setModified(False)
        self._baseline = _normalized(self._text)
        self._panel.setVisible(True)

        # Keep the preview exactly where it was, and make the editor the same
        # width instead of a fixed 1700 px that dwarfs it and squeezes the
        # window. The window grows by at most that much.
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry().width() if screen else EDIT_WIDTH
        editor_width = min(EDIT_WIDTH, self._preview_width)
        toc_w = self._outline_width()
        target = min(self._preview_width + editor_width + toc_w, int(available * 0.96))
        if self.width() < target:
            # QByteArray, not QRect: this is what restoreGeometry expects
            self._view_geometry = self.saveGeometry()
            self.resize(target, self.height())
        self._apply_split()
        QTimer.singleShot(0, self._apply_split)  # once the layout knows the child

        self._edit_btn.setText("Выйти из правки")
        self._editor.setFocus()
        self._set_editor_zoom(self._editor_zoom)
        self._move_editor_to_preview()
        self._update_hits(self._find_edit.text())  # search now targets the editor
        self._show_overlay()  # move it over the (now narrower) preview at once
        self._update_state()

    def _move_editor_to_preview(self) -> None:
        """Put the editor caret on the section the preview is currently showing.

        The preview renders HTML and the editor holds the original markdown, so
        they are matched by heading text, not by offset.
        """
        if not self._outline:
            self._editor.moveCursor(QTextCursor.MoveOperation.Start)
            return
        if not self._outline_ready:
            self._measure_outline()
        scroll = self._browser.verticalScrollBar().value()
        current = 0
        for index, offset in enumerate(self._outline_offset):
            if offset <= scroll + 24:
                current = index
            else:
                break
        label = self._outline[current].label
        cursor = self._editor.document().find(label)
        if cursor.isNull():
            cursor = self._editor.document().find(label.split(" ")[0])
        if not cursor.isNull():
            self._editor.setTextCursor(cursor)
            self._editor.centerCursor()

    def _leave_edit(self) -> None:
        if not self._confirm_discard():
            return
        self._ensure_overlay()
        self._edit_mode = False
        self._panel.setVisible(False)
        self._apply_split()
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
        self._update_hits(self._find_edit.text())  # search back to the preview
        self._show_overlay()  # back over the full-width preview
        self._update_state()

    def _apply_split(self) -> None:
        """Lay out the splitter according to what is currently visible:

            read mode:  [toc?] [preview]
            edit mode:  [toc?] [preview] [editor]
        """
        total = self.width()
        editor = self._editor is not None and self._panel.isVisible()
        toc = self._toc.isVisible()
        toc_w = self._outline_width()
        if editor:
            rest = total - toc_w
            if self._preview_width:
                preview = min(self._preview_width, max(MIN_COLUMN, rest - MIN_COLUMN))
            else:
                preview = int(rest * EDIT_SPLIT)
            sizes = ([toc_w] if toc else []) + [preview, rest - preview]
        else:
            sizes = ([toc_w] if toc else []) + [total - toc_w]
        self._splitter.setSizes(sizes)
        if getattr(self, "_overlay", None) is not None and self._overlay.isVisible():
            self._place_overlay()
        # setSizes is relative to the splitter's *current* total; once the window
        # has its real size, lay it out again with the absolute numbers
        QTimer.singleShot(0, self._apply_split_settled)
        QTimer.singleShot(0, self._set_margins)

    def _apply_split_settled(self) -> None:
        total = self._splitter.width()
        if total <= 0 or total == self.width():
            return
        self._apply_split()

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
        if self._convert_btn is not None:
            self._convert_btn.setEnabled(editing and text != self._rendered)
        if self._save_btn is not None:
            self._save_btn.setEnabled(unsaved)

    def _handle_esc(self) -> None:
        if self._find_edit is not None and self._find_edit.hasFocus():
            self._close_find()
        elif self._editing():
            self._leave_edit()

    # -- overlay (hover chrome) -------------------------------------------

    def _build_find_bar(self) -> None:
        """The find row lives inside the overlay, not a status bar."""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Найти в документе")
        self._find_edit.setFixedWidth(220)
        self._find_edit.returnPressed.connect(lambda: self._find_next())
        self._find_edit.textChanged.connect(self._update_hits)

        self._find_status = QLabel("")
        self._find_status.setStyleSheet(f"color: {TEXT_MUTED};")

        prev_btn = QToolButton()
        prev_btn.setText("↑")
        prev_btn.clicked.connect(lambda: self._find_next(backward=True))

        next_btn = QToolButton()
        next_btn.setText("↓")
        next_btn.clicked.connect(lambda: self._find_next())

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.clicked.connect(self._close_find)

        for widget in (prev_btn, self._find_edit, next_btn, self._find_status, close_btn):
            layout.addWidget(widget)

        self._find_bar = bar
        # the field stays in the overlay for good: showing/hiding it made the
        # overlay change width long after the search was over
        bar.setVisible(True)

    def _ensure_overlay(self) -> None:
        """Build the hover chrome on first need (it is not free: the first
        styled widget warms up QStyleSheetStyle for ~300 ms)."""
        if self._overlay is not None:
            return
        self._build_find_bar()
        self._build_edit_button()
        self._build_overlay()

    def _build_overlay(self) -> None:
        """A floating control strip over the preview, shown on hover.

        It is a child of the main window rather than a layout item, so it can
        sit on top of the splitter and never steals space from the text.
        """
        panel = QWidget(self)
        panel.setObjectName("overlay")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(4)

        self._toc_btn = QToolButton()
        self._toc_btn.setText("☰")
        self._toc_btn.clicked.connect(self._toggle_outline)

        search_btn = QToolButton()
        search_btn.setText("🔍")
        search_btn.clicked.connect(self._toggle_find)

        open_btn = QToolButton()
        open_btn.setText("📂")
        open_btn.clicked.connect(self._open_file)

        zoom_out = QToolButton()
        zoom_out.setText("−")
        zoom_out.clicked.connect(lambda: self._set_zoom(self._zoom - 1))

        self._zoom_label = QToolButton()
        self._zoom_label.setText("100%")
        self._zoom_label.clicked.connect(self._reset_zoom)

        zoom_in = QToolButton()
        zoom_in.setText("+")
        zoom_in.clicked.connect(lambda: self._set_zoom(self._zoom + 1))

        for widget in (
            self._toc_btn,
            self._edit_btn,
            self._find_bar,
            self._zoom_label,
            search_btn,
            open_btn,
            zoom_out,
            zoom_in,
        ):
            row.addWidget(widget)
        row.addStretch(1)

        panel.setVisible(False)
        self._overlay = panel

    def _show_overlay(self) -> None:
        self._ensure_overlay()
        self._overlay.setVisible(True)
        self._overlay.raise_()
        self._place_overlay()

    def _place_overlay(self) -> None:
        """Centre the overlay over the preview pane, near its top edge."""
        browser = self._browser
        top_left = browser.mapTo(self, browser.rect().topLeft())
        width = min(self._overlay.sizeHint().width(), browser.width() - 24)
        x = top_left.x() + max(12, (browser.width() - width) // 2)
        self._overlay.resize(width, self._overlay.sizeHint().height())
        self._overlay.move(x, top_left.y() + 12)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._show_overlay()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        # hide only when the pointer really left the window, not just the
        # central widget (the overlay itself counts as inside)
        if not self.underMouse() and self._overlay is not None:
            self._overlay.setVisible(False)

    def _toggle_find(self) -> None:
        self._show_overlay()
        self._find_edit.setFocus()
        self._find_edit.selectAll()
        self._update_hits(self._find_edit.text())

    def _close_find(self) -> None:
        """Clear the query; the field itself stays in the overlay."""
        self._find_edit.clear()
        self._find_status.setText("")
        self._browser.setFocus()

    def _search_pattern(self, text: str) -> QRegularExpression | None:
        """Whitespace-insensitive query.

        Qt collapses runs of spaces when it renders HTML, so a query copied
        with two spaces would never match the document text. Every run of
        whitespace is matched as one, and leading/trailing spaces are dropped
        so a pasted fragment still finds the words.
        """
        collapsed = re.sub(r"\s+", " ", text.strip())
        if not collapsed:
            return None
        escaped = QRegularExpression.escape(collapsed)
        # QRegularExpression.escape turns a space into "\ ": let one space in
        # the query match any run of whitespace in the document
        return QRegularExpression(escaped.replace("\\ ", r"\s+"))

    def _search_target(self):
        """The pane the search works on: the editor while editing, else preview."""
        if self._editing() and self._editor is not None:
            return self._editor
        return self._browser

    def _target_text(self, target) -> str:
        if target is self._editor:
            return target.toPlainText()
        return target.document().toPlainText()

    def _update_hits(self, text: str) -> None:
        pattern = self._search_pattern(text)
        if pattern is None:
            self._find_status.setText("")
            return
        matches = pattern.globalMatch(self._target_text(self._search_target()))
        hits = 0
        while matches.hasNext():
            matches.next()
            hits += 1
        self._find_status.setText(str(hits))
        if hits:
            # jump to the first match as the user types, not only on Enter
            self._find_first()

    def _find_first(self) -> None:
        """Select the first match in the active pane."""
        pattern = self._search_pattern(self._find_edit.text())
        if pattern is None:
            return
        target = self._search_target()
        cursor = target.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        target.setTextCursor(cursor)
        target.find(pattern, QTextDocument.FindFlag(0))

    def _find_next(self, backward: bool = False) -> None:
        pattern = self._search_pattern(self._find_edit.text())
        if pattern is None:
            return
        target = self._search_target()
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if target.find(pattern, flags):
            return
        # wrap around: start from the document edge and search again
        cursor = target.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start
        )
        target.setTextCursor(cursor)
        target.find(pattern, flags)

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
            "Ctrl+B": self._toggle_outline,
            "Ctrl+Return": self._convert,
            "Ctrl+S": self._save,
            "Ctrl+=": lambda: self._zoom_step(1),
            "Ctrl++": lambda: self._zoom_step(1),
            "Ctrl+-": lambda: self._zoom_step(-1),
            "Ctrl+0": lambda: self._zoom_step(0),
        }
        for keys, slot in actions.items():
            QShortcut(QKeySequence(keys), self, slot)

    def _zoom_step(self, step: int) -> None:
        """Zoom the pane the user is working in: editor while editing, else
        the preview. The two factors are independent."""
        if self._editing() and self._editor.hasFocus():
            target = self._editor_zoom + step if step else 0
            self._set_editor_zoom(target)
        else:
            target = self._zoom + step if step else 0
            self._set_zoom(target)

    def _set_zoom(self, level: int) -> None:
        """Preview zoom only; the editor has its own (`_set_editor_zoom`)."""
        level = max(-8, min(20, level))
        delta = level - self._zoom
        for _ in range(abs(delta)):
            self._browser.zoomIn(1 if delta > 0 else -1)
        self._zoom = level
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        if self._zoom_label is None:
            return
        self._zoom_label.setText(f"{round((1.0 + 0.1 * self._zoom) * 100)}%")

    def _reset_zoom(self) -> None:
        self._set_zoom(0)

    def _set_editor_zoom(self, level: int) -> None:
        """Editor zoom only, independent of the preview."""
        self._editor_zoom = max(-8, min(20, level))
        self._apply_editor_zoom()
        self._update_editor_zoom_label()

    def _apply_editor_zoom(self) -> None:
        if self._editor is None or self._editor_base_font is None:
            return
        font = QFont(self._editor_base_font)  # QFont is mutable: never edit in place
        if self._editor_zoom:
            size = font.pointSizeF() * (1.0 + 0.1 * self._editor_zoom)
            font.setPointSizeF(max(6.0, size))
        self._editor.setFont(font)

    def _update_editor_zoom_label(self) -> None:
        if getattr(self, "_editor_zoom_label", None) is None:
            return
        self._editor_zoom_label.setText(f"{round((1.0 + 0.1 * self._editor_zoom) * 100)}%")

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