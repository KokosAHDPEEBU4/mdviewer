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

from PyQt6.QtCore import QEvent, QPoint, QRegularExpression, QRect, QSettings, QTimer, QUrl, Qt
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
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
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from renderer import Heading, render_with_outline
from syncsel import SourceMap
from theme import (
    EDITOR_HARD,
    EDITOR_MIN,
    EDITOR_PREF,
    EDITOR_SELECTION,
    HYST,
    MIN_COLUMN,
    PREVIEW_HARD,
    PREVIEW_SELECTION,
    SCREEN_FALLBACK,
    SPLIT_HANDLE,
    SURFACE,
    TEXT_MUTED,
    TOC_MIN_WIDTH,
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


def layout_panes(avail: int, edit: bool, toc_visible: bool, toc_pref: int, editor_pref: int):
    """Split `avail` px (splitter handles already removed) between the panes.

    Returns `(toc, editor, preview)`. Pure, so the shrink policy can be traced in
    tests. When space is short the panes yield in priority order TOC -> editor ->
    preview: the outline shrinks first, then the editor through its soft minimum
    down to its hard floor, and only then does the preview drop below
    `MIN_COLUMN`. The preview is the residual pane, so any surplus goes to it.
    """
    avail = max(0, int(avail))
    toc = max(TOC_MIN_WIDTH, int(toc_pref)) if toc_visible else 0
    editor = int(editor_pref) if edit else 0
    deficit = (toc + editor + MIN_COLUMN) - avail
    if deficit > 0 and toc > 0:
        take = min(deficit, max(0, toc - TOC_MIN_WIDTH))
        toc -= take
        deficit -= take
    if deficit > 0 and editor > 0:
        take = min(deficit, max(0, editor - EDITOR_MIN))
        editor -= take
        deficit -= take
    if deficit > 0 and editor > 0:
        take = min(deficit, max(0, editor - EDITOR_HARD))
        editor -= take
        deficit -= take
    preview = avail - toc - editor
    if preview < PREVIEW_HARD:
        preview = PREVIEW_HARD
        editor = max(0, avail - toc - preview)
    return toc, editor, preview


def toc_visible_for(main_avail: int, auto_hidden: bool, user_visible: bool, edit: bool) -> bool:
    """Whether the outline should be shown, with hysteresis on the edge.

    `main_avail` is the splitter content width minus the main panes' handles (so
    it does not change when the outline itself appears — no feedback loop).
    """
    if not user_visible:
        return False
    main_min = (EDITOR_MIN + MIN_COLUMN + SPLIT_HANDLE) if edit else MIN_COLUMN
    threshold = TOC_MIN_WIDTH + main_min
    if auto_hidden:
        return main_avail > threshold + HYST
    return main_avail >= threshold


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


class ScrollIndicator(QWidget):
    """The little anchor circle shown where a middle-click starts autoscroll.

    Purely decorative: it is a child overlay of the scrolled pane and draws the
    browser-style ring plus a dot that follows the pointer.
    """

    RADIUS = 22

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._origin = QPoint(0, 0)
        self._dot = QPoint(0, 0)
        self.hide()

    def start(self, origin: QPoint) -> None:
        self._origin = origin
        self._dot = QPoint(0, 0)
        size = self.RADIUS * 2
        self.setGeometry(origin.x() - self.RADIUS, origin.y() - self.RADIUS, size, size)
        self.show()
        self.raise_()

    def set_dot(self, offset: QPoint) -> None:
        limit = self.RADIUS - 5
        self._dot = QPoint(
            max(-limit, min(limit, offset.x())),
            max(-limit, min(limit, offset.y())),
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = self.rect().center()
        painter.setPen(QPen(QColor(210, 210, 220, 180), 2))
        painter.drawEllipse(centre, self.RADIUS - 1, self.RADIUS - 1)
        painter.setBrush(QColor(210, 210, 220, 200))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(centre + self._dot, 4, 4)


class Viewer(QMainWindow):
    SCROLL_DEAD_ZONE = 12   # px the pointer must move before autoscroll kicks in
    WHEEL_STEP = 40.0       # accumulated wheel units that make one zoom level

    def __init__(self, path: Path, settings: QSettings | None = None):
        super().__init__()
        self._path = Path(path).resolve()
        self._settings = settings or QSettings()
        self._side = -1
        self._zoom = 0
        self._editor_zoom = 0
        self._toc_zoom = 0
        self._toc_base_font = None
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
        self._history: list[Path] = []   # files visited before the current one
        self._selected_section = None    # outline row the user last clicked
        # pane shrink policy state (see `_relayout`): the user's wish to see the
        # outline is kept apart from the fact that it is auto-hidden on a narrow
        # window, so widening can bring it back without overriding the user
        self._toc_user_visible = True
        self._toc_auto_hidden = False
        self._editor_pref = EDITOR_PREF
        self._in_relayout = False
        self._edit_anchor = None     # label of the visible section when editing starts
        self._edit_visible = ""      # text of the paragraph at the viewport top
        self._edit_fraction = None   # how far into the section the viewport top is
        self._preview_anchor = None  # preview character to keep pinned across relayout
        self._hl_layers = {}          # per-pane extra selections (mutual highlighting)
        self._map_source = None        # source text the position map is built for
        self._source_map = None        # lazy SourceMap (source <-> rendered text)
        self._map_failed = False
        self._map_dirty = False        # editor edited since the last render
        self._toc_syncing = False
        self._wheel_accum = {}        # per-pane leftover wheel delta for smooth zoom
        # middle-click autoscroll: one pane at a time, tracked per pane
        self._autoscroll_pane = None
        self._autoscroll_origin = QPoint(0, 0)
        self._autoscroll_offset = QPoint(0, 0)
        self._scroll_indicator = None
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)

        self.setWindowTitle(self._title(False))
        self.resize(1000, 800)

        self._browser = QTextBrowser()
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        # Let every click come through `anchorClicked`: Qt's own handling would
        # either `setSource` the raw markdown (no styling) or hand the file to
        # another program. We navigate inside the window instead.
        self._browser.setOpenLinks(False)
        self._browser.setOpenExternalLinks(False)
        self._browser.anchorClicked.connect(self._open_link)
        # hard floor (safety net for the shrink policy): the preview never
        # collapses to a sliver, and this gives the window its minimum width
        self._browser.setMinimumWidth(PREVIEW_HARD)
        self._browser.installEventFilter(self)
        # wheel and mouse events are delivered to the viewport of a scroll area,
        # not to the scroll area itself, so the filter has to sit there too:
        # otherwise Qt's built-in Ctrl+wheel zoom moved the font behind our back
        self._browser.viewport().installEventFilter(self)
        self._browser.viewport().setMouseTracking(True)  # autoscroll follows the pointer
        self._browser.verticalScrollBar().valueChanged.connect(self._sync_outline)
        self._browser.selectionChanged.connect(self._highlight_editor_from_preview)
        self._preview_base_font = QFont(self._browser.document().defaultFont())

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        # a wide handle is much easier to catch with the mouse than the ~5 px default
        self._splitter.setHandleWidth(SPLIT_HANDLE)
        self._splitter.addWidget(self._build_outline())
        self._splitter.addWidget(self._browser)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        # the divider changes the preview width without resizing the window, so
        # no resizeEvent arrives and the insets would keep their old value
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        # the splitter resizes a step after the window, so its own Resize event
        # (which carries the final size) drives the pane layout
        self._splitter.installEventFilter(self)
        self.setCentralWidget(self._splitter)

        self._edit_btn = None
        self._back_btn = None
        self._toc_btn = None
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

    def load(self) -> bool:
        if not self._confirm_discard():
            return False
        self._text, self._encoding, self._newline = read_document(self._path)
        self._clear_highlights()
        self._selected_section = None  # the outline is rebuilt for this file
        self._render(self._text)
        self._baseline = _normalized(self._text)
        if self._editor is not None:
            self._editor.setPlainText(self._text)
            self._editor.document().setModified(False)
            self._map_dirty = False  # matches the render that just happened
        self._update_state()
        return True

    def _render(self, text: str) -> None:
        """Re-render the preview. Called only on load/convert/save, never on
        resize (that would cost hundreds of milliseconds per step)."""
        # setHtml rebuilds the document, so any stored highlight cursor would
        # dangle; drop the layers first
        self._clear_highlights()
        self._rendered = _normalized(text)
        # the stylesheet has to be in place before the HTML: Qt matches CSS while
        # importing, not afterwards
        self._browser.document().setDefaultStyleSheet(content_css())
        html, self._outline = render_with_outline(text, self._path.parent)
        self._browser.setHtml(html)
        # relative links (`other.md`, `#anchor`) resolve against the current
        # file so the click handler can find the target on disk
        self._browser.document().setBaseUrl(QUrl.fromLocalFile(str(self._path)))
        # the position map matches this render; built lazily on first selection
        self._map_source = _normalized(text)
        self._source_map = None
        self._map_failed = False
        self._map_dirty = False
        self._apply_preview_zoom()  # setHtml resets the document font
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
        """A user drag: remember the pane widths as the new preferences.

        `setSizes` does not emit `splitterMoved`, so anything seen here came from
        the mouse; the `_in_relayout` guard is belt-and-braces. The preview is the
        residual pane and has no preference of its own — dragging the editor
        boundary only moves `_editor_pref`.
        """
        self._set_margins()
        if self._in_relayout:
            return
        sizes = self._splitter.sizes()
        if not sizes:
            return
        if self._toc.isVisible():
            self._settings.setValue("toc_width", max(TOC_MIN_WIDTH, sizes[0]))
        if self._editing() and self._panel is not None and self._panel.isVisible():
            self._editor_pref = max(EDITOR_HARD, sizes[-1])

    # -- table of contents -------------------------------------------------

    def _build_outline(self) -> QWidget:
        self._toc = QListWidget()
        self._toc.setObjectName("outline")
        self._toc.setUniformItemSizes(False)
        self._toc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._toc.setMinimumWidth(TOC_MIN_WIDTH)
        # long headings wrap onto the next line instead of being elided with "…"
        self._toc.setWordWrap(True)
        self._toc.itemClicked.connect(self._outline_clicked)
        self._toc.installEventFilter(self)
        self._toc.viewport().installEventFilter(self)  # wheel lands on the viewport
        self._toc_base_font = self._toc.font()
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
        self._toc_user_visible = (
            self._settings.value("toc_visible", True, type=bool) and bool(self._outline)
        )
        self._toc_auto_hidden = False  # a freshly rendered document starts shown
        self._relayout()
        QTimer.singleShot(0, self._sync_outline)

    def _toc_pref(self) -> int:
        """Width the user wants the outline to have (default `TOC_WIDTH`)."""
        return max(TOC_MIN_WIDTH, int(self._settings.value("toc_width", TOC_WIDTH)))

    def _set_outline_visible(self, visible: bool) -> None:
        """User intent: show/hide the outline. Auto-hide never overrides this."""
        self._toc_user_visible = bool(visible)
        self._settings.setValue("toc_visible", self._toc_user_visible)
        self._toc_auto_hidden = False
        self._relayout()
        QTimer.singleShot(0, self._sync_outline)

    def _toc_can_toggle(self) -> bool:
        """False when turning the outline on now would just auto-hide it again."""
        if not self._outline:
            return False
        if self._toc.isVisible():
            return True
        edit = self._editing()
        main_panes = 2 if edit else 1
        main_avail = self._splitter.width() - SPLIT_HANDLE * (main_panes - 1)
        return toc_visible_for(main_avail, True, True, edit)

    def _toggle_outline(self) -> None:
        if not self._toc_can_toggle():
            return
        self._set_outline_visible(not self._toc_user_visible)

    def _outline_clicked(self, item: QListWidgetItem) -> None:
        self._selected_section = self._toc.row(item)
        anchor = item.data(Qt.ItemDataRole.UserRole)
        self._browser.scrollToAnchor(anchor)
        self._sync_outline()

    def _remembered_section(self):
        """Outline row the user last jumped to, while it is still on screen.

        Relying only on the top viewport pixel dropped the reader into the
        enclosing section when the wanted sub-heading sat a little below the
        edge. The row the user picked (usually in the outline) is the stronger
        signal; it is ignored once its heading has scrolled out of view.
        """
        if self._selected_section is None or not self._outline:
            return None
        if not 0 <= self._selected_section < len(self._outline):
            return None
        block = self._block_for_anchor(self._outline[self._selected_section].anchor)
        if block is None:
            return None
        rect = self._browser.document().documentLayout().blockBoundingRect(block)
        top = self._browser.verticalScrollBar().value()
        bottom = top + self._browser.viewport().height()
        if rect.bottom() >= top and rect.top() <= bottom:
            return self._selected_section
        return None

    def _current_section_index(self) -> int:
        """Index of the heading on screen, from a cursor at the top of the
        viewport walking back to the nearest anchored heading.

        Earlier this used `blockBoundingRect` offsets, but they go stale as soon
        as the document re-wraps (a resize, entering edit mode), so the section
        is derived from the viewport cursor instead.
        """
        cursor = self._browser.cursorForPosition(self._browser.viewport().rect().topLeft())
        index = self._anchored_index(cursor.block())
        return index if index is not None else 0

    @staticmethod
    def _anchored_index(block):
        """Outline index of the nearest `md-hN` anchor at or above `block`."""
        while block.isValid():
            fragment = block.begin()
            while fragment != block.end():
                piece = fragment.fragment()
                if piece.isValid():
                    for name in piece.charFormat().anchorNames():
                        if name.startswith("md-h"):
                            return int(name[4:])
                fragment += 1
            block = block.previous()
        return None

    def _current_section_label(self) -> str | None:
        """Label of the section currently at the top of the preview."""
        if not self._outline:
            return None
        return self._outline[self._current_section_index()].label

    def _sync_outline(self) -> None:
        """Highlight the section that owns the top of the viewport."""
        if self._toc_syncing or not self._toc.isVisible() or not self._outline:
            return
        current = self._current_section_index()
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
        QTimer.singleShot(0, self._relayout)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_margins()
        if getattr(self, "_overlay", None) is not None and self._overlay.isVisible():
            self._place_overlay()

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
        """Route a link click: markdown files open in this window, everything
        else goes to the system.

        `setOpenLinks(False)` means this is the only place a click is handled,
        so a linked ``.md`` is rendered with the full theme (Qt's fallback would
        show it as plain text) and the visit is remembered for "Назад".
        """
        target = self._link_target(url)
        if target is not None:
            if target == self._path:
                if url.hasFragment():  # an in-document anchor
                    self._browser.scrollToAnchor(url.fragment())
            elif not target.is_file():
                return  # a broken local link: nothing sensible to open
            elif self._viewable(target):
                fragment = url.fragment()
                self._navigate(target)
                if fragment:  # e.g. other.md#section
                    QTimer.singleShot(0, lambda: self._browser.scrollToAnchor(fragment))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            return
        if url.hasFragment() and not url.path():
            self._browser.scrollToAnchor(url.fragment())
            return
        QDesktopServices.openUrl(url)

    def _link_target(self, url: QUrl) -> Path | None:
        """Local path a link points at, or None for a remote/non-file URL."""
        if url.isLocalFile():
            return Path(url.toLocalFile())
        if not url.scheme() and url.path():
            return (self._path.parent / url.path()).resolve()
        return None

    @staticmethod
    def _viewable(path: Path) -> bool:
        return path.suffix.lower() in {".md", ".markdown", ".txt"}

    # -- history (linked files, "Назад") ----------------------------------

    def _navigate(self, path: Path) -> None:
        """Open `path` in this window, remembering where we came from."""
        target = Path(path).resolve()
        if target == self._path:
            return
        previous = self._path
        if not self._load_path(target):
            return
        self._history.append(previous)
        self._update_nav()

    def _go_back(self) -> None:
        """Return to the previously visited file."""
        if not self._history:
            return
        if not self._load_path(self._history[-1]):
            return
        self._history.pop()
        self._update_nav()

    def _load_path(self, path: Path) -> bool:
        """Point the window at `path`; False and no change when the user cancels."""
        previous = self._path
        self._path = Path(path).resolve()
        self.setWindowTitle(self._title(False))
        if self.load():
            return True
        self._path = previous
        self.setWindowTitle(self._title(False))
        return False

    def _update_nav(self) -> None:
        if self._back_btn is not None:
            self._back_btn.setEnabled(bool(self._history))

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
        self._editor.setMinimumWidth(EDITOR_HARD)  # hard floor, see theme metrics
        self._editor.setPlaceholderText("Исходный Markdown — как в блокноте")
        self._editor.textChanged.connect(self._update_state)
        self._editor.textChanged.connect(self._mark_map_dirty)
        self._editor.installEventFilter(self)
        self._editor.viewport().installEventFilter(self)  # wheel lands on the viewport
        self._editor.viewport().setMouseTracking(True)  # autoscroll follows the pointer
        self._editor.selectionChanged.connect(self._highlight_preview_from_editor)
        self._editor_base_font = self._editor.font()
        layout.addWidget(self._editor, 1)

        self._panel = panel
        # the header row of buttons wants ~450 px; without an explicit minimum
        # the whole editor pane could not shrink below that and QSplitter would
        # steal from the preview instead (see the shrink policy in `_relayout`)
        panel.setMinimumWidth(EDITOR_HARD)
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
        # the preview column shrinks a little when the editor opens (the window
        # may be capped by the screen), which rewraps the whole document; pin the
        # character at the top so the reader is not thrown to another section
        self._preview_anchor = self._browser.cursorForPosition(
            self._browser.viewport().rect().topLeft()
        ).position()
        QTimer.singleShot(200, self._clear_preview_anchor)
        # remember the target *before* the layout changes below: the window
        # resize re-wraps the preview, so re-reading the viewport after the
        # resize would land elsewhere. A section the user picked in the outline
        # wins over the section at the top pixel (see `_remembered_section`).
        remembered = self._remembered_section()
        if remembered is not None:
            self._edit_anchor = self._outline[remembered].label
            self._edit_visible = self._edit_anchor
            self._edit_fraction = None
        else:
            self._edit_anchor = self._current_section_label()
            # the section alone is too coarse: inside a long section the caret
            # would jump to the heading. Capture the visible paragraph and how
            # far into the section the viewport sits, so the editor opens on the
            # same text.
            self._edit_visible = self._visible_paragraph()
            self._edit_fraction = self._section_fraction()
        # measure the preview before anything is added to the splitter: adding
        # the (still hidden) editor panel already hands it space
        self._preview_width = max(self._browser.width(), MIN_COLUMN)
        if self._editor is None:
            self._build_editor()
        self._edit_mode = True
        self._editor.setPlainText(self._text)
        self._editor.document().setModified(False)
        self._baseline = _normalized(self._text)
        self._map_dirty = False  # editor content matches the last render
        self._panel.setVisible(True)

        # Grow the window enough for the outline, the current preview and the
        # preferred editor width (+ handles), capped by the screen the window is
        # already on (not the primary one: on a multi-monitor desk that would size
        # for the wrong monitor and let the window spill off its own screen). If
        # the cap bites, `_relayout` shrinks the editor first, preview staying put.
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry().width() if screen else SCREEN_FALLBACK
        toc_w = self._toc_pref() if self._toc.isVisible() else 0
        panes = 2 + (1 if toc_w else 0)
        target = min(
            self._preview_width + self._editor_pref + toc_w + SPLIT_HANDLE * (panes - 1),
            int(available * 0.96),
        )
        if self.width() < target:
            # QByteArray, not QRect: this is what restoreGeometry expects
            self._view_geometry = self.saveGeometry()
            self.resize(target, self.height())
        self._relayout()
        QTimer.singleShot(0, self._relayout)  # once the layout knows the child

        self._edit_btn.setText("Выйти из правки")
        self._editor.setFocus()
        # open the source at the same zoom the preview is at right now
        self._set_editor_zoom(self._zoom)
        self._move_editor_to_preview()
        self._update_hits(self._find_edit.text())  # search now targets the editor
        self._highlight_editor_from_preview()  # keep an existing preview selection in view
        self._show_overlay()  # move it over the (now narrower) preview at once
        self._update_state()

    def _visible_paragraph(self) -> str:
        """Rendered text of the paragraph at the top of the preview viewport."""
        corner = self._browser.viewport().rect().topLeft()
        return self._browser.cursorForPosition(corner).block().text().strip()

    def _section_fraction(self):
        """How far the viewport top is into the current section (0..1).

        Markdown and HTML do not share offsets, but the *relative* position
        within a section is comparable: it is used to land the caret near the
        same place when the visible paragraph cannot be matched by text.
        """
        if not self._outline:
            return None
        index = self._current_section_index()
        first = self._block_for_anchor(self._outline[index].anchor)
        if first is None:
            return None
        layout = self._browser.document().documentLayout()
        top = layout.blockBoundingRect(first).top()
        if index + 1 < len(self._outline):
            following = self._block_for_anchor(self._outline[index + 1].anchor)
        else:
            following = None
        bottom = (
            layout.blockBoundingRect(following).top()
            if following is not None
            else self._browser.document().size().height()
        )
        scroll = self._browser.verticalScrollBar().value()
        span = max(1.0, float(bottom - top))
        return max(0.0, min(1.0, (scroll - top) / span))

    def _block_for_anchor(self, anchor: str):
        """Block carrying the heading anchor `md-hN`, or None."""
        block = self._browser.document().begin()
        while block.isValid():
            fragment = block.begin()
            while fragment != block.end():
                piece = fragment.fragment()
                if piece.isValid() and anchor in piece.charFormat().anchorNames():
                    return block
                fragment += 1
            block = block.next()
        return None

    def _move_editor_to_preview(self) -> None:
        """Put the editor caret on the paragraph the preview was showing.

        The exact section is captured in `_enter_edit` before the window
        resizes. The visible paragraph is matched first (markdown markup and
        wraplines make the raw lines differ), then the section heading, then the
        relative position inside the section.
        """
        block = None
        if self._edit_visible:
            block = self._find_block_by_text(self._edit_visible)
        if block is None and self._edit_anchor:
            block = self._find_heading_block(self._edit_anchor)
        if block is None:
            block = self._find_block_at_fraction()
        if block is not None:
            self._editor.setTextCursor(QTextCursor(block))
            # `centerCursor` forces the editor to lay out, so `cursorRect` is
            # valid; only then can the target be lifted to the top. Reading
            # `blockBoundingRect` straight after `setPlainText` returns 0 (the
            # editor is not laid out yet), which parked the view on line 1.
            # The vertical scrollbar counts *lines*, `cursorRect` is pixels, so
            # the offset is divided by the line height before scrolling.
            self._editor.centerCursor()
            line_height = max(1, self._editor.fontMetrics().lineSpacing())
            delta = round(self._editor.cursorRect().top() / line_height)
            bar = self._editor.verticalScrollBar()
            if delta > 0:
                bar.setValue(bar.value() + delta)
        else:
            self._editor.moveCursor(QTextCursor.MoveOperation.Start)

    @staticmethod
    def _heading_key(text: str) -> str:
        """Heading text without markdown markup or extra whitespace."""
        stripped = text.lstrip("#").strip()
        for char in ("`", "*", "_", "[", "]", "\\"):
            stripped = stripped.replace(char, "")
        return re.sub(r"\s+", " ", stripped).strip()

    @staticmethod
    def _plain_key(text: str) -> str:
        """Source line reduced to its visible text: markup and links removed,
        whitespace collapsed. Lets a preview paragraph match its source line."""
        stripped = text.strip()
        stripped = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
        stripped = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
        stripped = re.sub(r"^(#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)", "", stripped)
        stripped = re.sub(r"`([^`]*)`", r"\1", stripped)
        stripped = re.sub(r"(\*\*|__)(.+?)\1", r"\2", stripped)
        stripped = re.sub(r"(\*|_)(.+?)\1", r"\2", stripped)
        for char in ("`", "*", "_", "[", "]", "\\", "|", "#", ">"):
            stripped = stripped.replace(char, "")
        return re.sub(r"\s+", " ", stripped).strip()

    def _section_blocks(self):
        """Editor blocks of the captured section, heading included.

        Scoping the text search to the section stops an identical paragraph in
        a later section from matching before the one that is actually on screen.
        """
        if not self._edit_anchor:
            blocks = []
            block = self._editor.document().begin()
            while block.isValid():
                blocks.append(block)
                block = block.next()
            return blocks
        start = self._find_heading_block(self._edit_anchor)
        if start is None:
            return []
        blocks = []
        block = start
        while block.isValid():
            if block is not start and block.text().lstrip().startswith("#"):
                break
            blocks.append(block)
            block = block.next()
        return blocks

    def _find_block_by_text(self, text: str):
        """Block whose visible text matches the preview paragraph.

        Exact match first; then a prefix match, because the preview merges
        wrapped source lines and drops markup, so the two sides rarely agree
        word for word.
        """
        want = self._plain_key(text)
        if len(want) < 2:
            return None
        blocks = self._section_blocks()
        for block in blocks:
            if self._plain_key(block.text()) == want:
                return block
        if len(want) >= 6:
            for block in blocks:
                key = self._plain_key(block.text())
                if len(key) >= 6 and (key.startswith(want[:32]) or want.startswith(key[:32])):
                    return block
        return None

    def _find_block_at_fraction(self):
        """Block at roughly `_edit_fraction` into the captured section."""
        if self._edit_fraction is None or not self._edit_anchor:
            return None
        lines = self._section_blocks()
        if not lines:
            return None
        index = int(round(self._edit_fraction * (len(lines) - 1)))
        return lines[index]

    def _find_heading_block(self, label: str):
        """Block whose heading text matches `label` (markup ignored)."""
        want = self._heading_key(label)
        block = self._editor.document().begin()
        while block.isValid():
            text = block.text()
            if text.lstrip().startswith("#") and self._heading_key(text) == want:
                return block
            block = block.next()
        return None

    # -- mutual selection highlighting ------------------------------------

    def _highlight_editor_from_preview(self) -> None:
        """Reflect the preview selection in the editor (preview's colour)."""
        if self._editor is None:  # read mode: the editor does not exist yet
            return
        self._highlight_from(self._browser)

    def _highlight_preview_from_editor(self) -> None:
        """Reflect the editor selection in the preview (editor's colour)."""
        self._highlight_from(self._editor)

    def _highlight_from(self, source) -> None:
        """Stack the newest selection on top in both panes.

        Each pane keeps at most one layer per source: the other pane's latest
        selection and its own. The pane that just changed is moved to the top,
        so its colour wins on both sides while the other pane's earlier
        selection stays visible underneath (it is not replaced).
        """
        colour = PREVIEW_SELECTION if source is self._browser else EDITOR_SELECTION
        text = self._collapse(source.textCursor().selectedText())
        target = self._editor if source is self._browser else self._browser
        if target is not None:
            cursors = self._mirror_cursors(source, target, text) if len(text) >= 2 else []
            self._set_hl_layer(target, source, cursors, colour)
        own = [source.textCursor()] if len(text) >= 2 else []
        self._set_hl_layer(source, source, own, colour)

    def _mirror_cursors(self, source, target, needle: str):
        """Extra-selection cursors for the same fragment in `target`.

        The position map is exact and handles multi-line selections and repeated
        phrases, so it is tried first. When there is no usable map (raw HTML the
        aligner cannot follow), fall back to a text search bounded by the
        selection's section — a plain whole-document search picked a duplicate in
        another section.
        """
        source_map = self._ensure_map()
        if source_map is not None and not self._map_dirty:
            cursor = source.textCursor()
            if source is self._editor:
                span = source_map.to_render(cursor.selectionStart(), cursor.selectionEnd())
            else:
                span = source_map.to_source(cursor.selectionStart(), cursor.selectionEnd())
            cursors = self._range_cursors(target, span)
            if cursors:
                return cursors
        pattern = self._match_pattern(needle)
        if pattern is None:
            return []
        bounds = self._section_bounds(source, source.textCursor())
        if bounds is None:  # no section info: fall back to the whole document
            found = target.document().find(pattern)
        else:
            document, start, end = bounds
            found = self._find_between(document, pattern, start, end)
        return [found] if found is not None and not found.isNull() else []

    def _ensure_map(self):
        """Build (once per render) the source <-> rendered position map."""
        if self._source_map is not None or self._map_failed or self._map_source is None:
            return self._source_map
        source_map = SourceMap(self._map_source, self._browser.document().toPlainText())
        if not source_map:
            self._map_failed = True
            return None
        self._source_map = source_map
        return source_map

    @staticmethod
    def _range_cursors(pane, span):
        """Selection cursors for a character span, **one per block**.

        Qt expands a single cursor that crosses table cells to whole cells, so a
        mirror inside a table would light up entire cells (header included) that
        the user did not select. Slicing at block boundaries keeps each fragment
        inside one cell/paragraph, where selection stays character-exact.
        """
        if span is None:
            return []
        start, end = span
        document = pane.document()
        if start < 0 or end > document.characterCount() - 1 or end <= start:
            return []
        cursors = []
        block = document.findBlock(start)
        while block.isValid():
            block_start = block.position()
            block_end = block_start + block.length() - 1  # separator excluded
            segment_start = max(start, block_start)
            segment_end = min(end, block_end)
            if segment_end > segment_start:
                cursor = QTextCursor(document)
                cursor.setPosition(segment_start)
                cursor.setPosition(segment_end, QTextCursor.MoveMode.KeepAnchor)
                cursors.append(cursor)
            if block_end >= end:
                break
            block = block.next()
        return cursors

    def _section_bounds(self, source, cursor):
        """`(document, start_block, end_block)` of `source`'s section, in `target`."""
        if source is self._browser:
            index = self._anchored_index(cursor.block())
            if index is None or index >= len(self._outline):
                return None
            start = self._find_heading_block(self._outline[index].label)
            if start is None:
                return None
            return self._editor.document(), start, self._next_editor_heading(start)
        label = self._editor_section_label(cursor.block())
        index = self._preview_index_for_label(label) if label else None
        if index is None:
            return None
        start = self._block_for_anchor(self._outline[index].anchor)
        if start is None:
            return None
        return self._browser.document(), start, self._next_preview_heading(start)

    @staticmethod
    def _find_between(document, pattern, start, end):
        """First match at or after `start` and before `end` (exclusive)."""
        found = document.find(pattern, QTextCursor(start))
        if found.isNull():
            return None
        if end is not None and found.block().blockNumber() >= end.blockNumber():
            return None
        return found

    @staticmethod
    def _next_editor_heading(block):
        block = block.next()
        while block.isValid():
            if block.text().lstrip().startswith("#"):
                return block
            block = block.next()
        return None

    def _next_preview_heading(self, block):
        block = block.next()
        while block.isValid():
            fragment = block.begin()
            while fragment != block.end():
                piece = fragment.fragment()
                if piece.isValid() and any(
                    name.startswith("md-h") for name in piece.charFormat().anchorNames()
                ):
                    return block
                fragment += 1
            block = block.next()
        return None

    def _editor_section_label(self, block):
        """Heading key of the editor section the selection starts in, or None."""
        while block.isValid():
            text = block.text()
            if text.lstrip().startswith("#"):
                return self._heading_key(text)
            block = block.previous()
        return None

    def _preview_index_for_label(self, label: str):
        key = self._heading_key(label)
        for index, heading in enumerate(self._outline):
            if self._heading_key(heading.label) == key:
                return index
        return None

    @staticmethod
    def _match_pattern(needle: str) -> QRegularExpression | None:
        words = re.findall(r"\w+", needle, re.UNICODE)
        if not words:
            return None
        if len(words) == 1:
            body = re.escape(words[0])
        else:
            body = r"[^\w]{0,8}".join(re.escape(word) for word in words)
        return QRegularExpression(
            r"\b" + body + r"\b",
            QRegularExpression.PatternOption.UseUnicodePropertiesOption,
        )

    def _set_hl_layer(self, pane, owner, cursors, colour: str) -> None:
        """Replace `owner`'s layer in `pane` with `cursors` (or drop it).

        `cursors` is a list of selection cursors (a mirror is split per block so
        table cells are not over-highlighted). The layer moves to the end of the
        list, i.e. on top: extra selections are painted in order and over the
        pane's own native selection.
        """
        layers = [layer for layer in self._hl_layers.get(pane, []) if layer[0] is not owner]
        valid = [c for c in cursors if c is not None and not c.isNull() and c.hasSelection()]
        if valid:
            layers.append((owner, valid, colour))
        self._hl_layers[pane] = layers
        selections = [
            self._extra_selection(cursor, col)
            for _, layer_cursors, col in layers
            for cursor in layer_cursors
        ]
        pane.setExtraSelections(selections)

    @staticmethod
    def _extra_selection(cursor, colour: str) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor(colour))
        selection.format.setForeground(QColor("#ffffff"))
        return selection

    def _clear_highlights(self) -> None:
        self._hl_layers = {}
        self._browser.setExtraSelections([])
        if self._editor is not None:
            self._editor.setExtraSelections([])

    @staticmethod
    def _collapse(text: str) -> str:
        """Whitespace is collapsed on the HTML side, so collapse both sides."""
        return re.sub(r"\s+", " ", text.replace("\u2029", " ")).strip()

    def _leave_edit(self) -> None:
        if not self._confirm_discard():
            return
        self._ensure_overlay()
        self._preview_anchor = None  # entering-edit pin must not outlive the edit
        self._edit_mode = False
        self._panel.setVisible(False)
        self._clear_highlights()  # the editor is gone; drop its mirror
        self._relayout()
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

    def _relayout(self, content: int | None = None) -> None:
        """Lay the panes out for the current splitter width and mode.

        The policy itself lives in `layout_panes` / `toc_visible_for`; this only
        feeds them the real geometry and applies the result. Called on splitter
        resize, mode change and outline toggle. `content` is the splitter width
        (passed in from the Resize event so it is never stale).
        """
        if self._in_relayout:
            return
        self._in_relayout = True
        try:
            if content is None:
                content = self._splitter.width()
            edit = self._editing()
            main_panes = 2 if edit else 1
            # the outline's own visibility must not feed back into this number,
            # so only the handles of the main panes are removed here
            main_avail = content - SPLIT_HANDLE * (main_panes - 1)
            user_visible = self._toc_user_visible and bool(self._outline)
            toc_visible = toc_visible_for(main_avail, self._toc_auto_hidden, user_visible, edit)
            self._toc_auto_hidden = user_visible and not toc_visible
            if not user_visible:
                self._toc_auto_hidden = False
            self._toc.setVisible(toc_visible)
            panes = main_panes + (1 if toc_visible else 0)
            avail = max(0, content - SPLIT_HANDLE * (panes - 1))
            toc, editor, preview = layout_panes(
                avail, edit, toc_visible, self._toc_pref(), self._editor_pref
            )
            # `setSizes` indexes *children*, hidden ones included, so always pass
            # a full list (0 for a hidden pane) or the widths land on the wrong
            # widgets
            sizes = [toc if toc_visible else 0, preview, editor if edit else 0]
            self._splitter.setSizes(sizes)
            self._update_toc_toggle(toc_visible, main_avail)
            if getattr(self, "_overlay", None) is not None and self._overlay.isVisible():
                self._place_overlay()
        finally:
            self._in_relayout = False
        QTimer.singleShot(0, self._set_margins)
        # keep the reader where they were: the column width (and the viewport
        # margins) just changed, so a table-heavy document rewraps and the same
        # scroll pixel lands on a different section. Runs after `_set_margins`.
        QTimer.singleShot(0, self._restore_preview_anchor)

    def _update_toc_toggle(self, toc_visible: bool, main_avail: int) -> None:
        """Disable the outline toggle when it would just auto-hide again."""
        button = getattr(self, "_toc_btn", None)
        if button is None:
            return
        if not self._outline:
            button.setEnabled(False)
            return
        fits = toc_visible_for(main_avail, True, True, self._editing())
        button.setEnabled(toc_visible or fits)

    def _restore_preview_anchor(self) -> None:
        if self._preview_anchor is not None:
            self._scroll_preview_to(self._preview_anchor)

    def _clear_preview_anchor(self) -> None:
        self._preview_anchor = None

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

    def _mark_map_dirty(self) -> None:
        """The editor changed, so its offsets no longer match the last render."""
        self._map_dirty = True

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
        if self._autoscroll_timer.isActive():
            self._stop_autoscroll()
        elif self._find_edit is not None and self._find_edit.hasFocus():
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
        self._find_edit.textChanged.connect(lambda text: self._update_hits(text, jump=True))

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

        self._back_btn = QToolButton()
        self._back_btn.setText("←")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setEnabled(bool(self._history))

        self._toc_btn = QToolButton()
        self._toc_btn.setText("☰")
        self._toc_btn.clicked.connect(self._toggle_outline)
        self._toc_btn.setEnabled(self._toc_can_toggle())

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
            self._back_btn,
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

    def _pane_for(self, obj):
        """The pane a filtered object belongs to (the widget or its viewport)."""
        if obj is self._browser or obj is self._browser.viewport():
            return self._browser
        if obj is self._toc or obj is self._toc.viewport():
            return self._toc
        if self._editor is not None and (obj is self._editor or obj is self._editor.viewport()):
            return self._editor
        return None

    def eventFilter(self, obj, event):
        """Per-pane chrome: Ctrl+wheel zooms, middle-click autoscrolls, and the
        splitter's own resize drives the pane shrink policy.

        The filter is installed on both the pane and its viewport: a scroll area
        delivers wheel and mouse events to the viewport, so filtering only the
        widget would let Qt's built-in Ctrl+wheel zoom run unnoticed (then the
        font moved but `_zoom` and the label stayed at 100%). Autoscroll is
        tracked separately per pane, so starting it in one never moves the other.
        """
        if obj is self._splitter and event.type() == QEvent.Type.Resize:
            # the splitter resizes a step after the window, so its Resize carries
            # the final width; laying out here avoids a stale-width pass
            self._relayout(event.size().width())
            return False
        pane = self._pane_for(obj)
        if pane is None:
            return super().eventFilter(obj, event)
        etype = event.type()
        if pane in (self._browser, self._editor):
            if etype == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self._wheel_zoom(pane, event)
                    return True
                self._stop_autoscroll()
            elif etype == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.MiddleButton:
                    self._start_autoscroll(pane, event.position().toPoint())
                    return True
                self._stop_autoscroll()
            elif etype == QEvent.Type.MouseMove and self._autoscroll_pane is pane:
                self._move_autoscroll(event.position().toPoint())
                return True
        elif pane is self._toc and etype == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._wheel_zoom(self._toc, event)
                return True
        return super().eventFilter(obj, event)

    # -- zoom (shared by wheel, buttons and shortcuts) ---------------------

    def _wheel_delta(self, event) -> float:
        """Wheel movement as a signed amount in `WHEEL_STEP` units.

        Trackpads report a fine `pixelDelta`; a mouse wheel reports `angleDelta`
        in eighths of a degree (120 per notch). Normalising both keeps the zoom
        responsive and even, instead of one level per event.
        """
        delta = event.pixelDelta().y()
        if delta:
            return float(delta)
        return event.angleDelta().y() / 120.0 * self.WHEEL_STEP

    def _wheel_zoom(self, pane, event) -> None:
        """Zoom `pane` by whole levels, carrying the rest to the next event."""
        accum = self._wheel_accum.get(pane, 0.0) + self._wheel_delta(event)
        step = int(accum / self.WHEEL_STEP)
        self._wheel_accum[pane] = accum - step * self.WHEEL_STEP
        if not step:
            return
        if pane is self._editor:
            self._set_editor_zoom(self._editor_zoom + step)
        elif pane is self._toc:
            self._set_toc_zoom(self._toc_zoom + step)
        else:
            self._set_zoom(self._zoom + step)

    # -- middle-click autoscroll ------------------------------------------

    def _start_autoscroll(self, pane, point: QPoint) -> None:
        if self._autoscroll_pane is pane and self._autoscroll_timer.isActive():
            self._stop_autoscroll()
            return
        self._stop_autoscroll()
        self._autoscroll_pane = pane
        if self._scroll_indicator is None:
            self._scroll_indicator = ScrollIndicator(pane.viewport())
        else:
            self._scroll_indicator.setParent(pane.viewport())
        self._scroll_indicator.start(point)
        self._autoscroll_origin = point
        self._autoscroll_timer.start(16)

    def _move_autoscroll(self, point: QPoint) -> None:
        self._autoscroll_offset = point - self._autoscroll_origin
        if self._scroll_indicator is not None:
            self._scroll_indicator.set_dot(self._autoscroll_offset)

    def _stop_autoscroll(self) -> None:
        if self._autoscroll_timer.isActive():
            self._autoscroll_timer.stop()
        if self._scroll_indicator is not None:
            self._scroll_indicator.hide()
        self._autoscroll_pane = None
        self._autoscroll_offset = QPoint(0, 0)

    def _autoscroll_tick(self) -> None:
        pane = self._autoscroll_pane
        if pane is None:
            self._stop_autoscroll()
            return
        # dead zone so a tiny wobble does not scroll; speed grows with distance
        dy = self._autoscroll_offset.y()
        step = 0
        if abs(dy) > self.SCROLL_DEAD_ZONE:
            over = abs(dy) - self.SCROLL_DEAD_ZONE
            step = int((over / 3 + 1) * (1 if dy > 0 else -1))
        if step:
            bar = pane.verticalScrollBar()
            bar.setValue(bar.value() + step)

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

    def _update_hits(self, text: str, jump: bool = False) -> None:
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
        if hits and jump:
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
            "Alt+Left": self._go_back,
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
        """Zoom the pane the user is working in. All three are independent."""
        if self._toc.isVisible() and self._toc.hasFocus():
            target = self._toc_zoom + step if step else 0
            self._set_toc_zoom(target)
        elif self._editing() and self._editor.hasFocus():
            target = self._editor_zoom + step if step else 0
            self._set_editor_zoom(target)
        else:
            target = self._zoom + step if step else 0
            self._set_zoom(target)

    def _set_zoom(self, level: int) -> None:
        """Preview zoom only; scale the document font, not `zoomIn/Out`.

        `zoomIn` multiplies the current size, so repeated steps drift and
        "100%" never returns to the real base. Scaling from the saved base font
        keeps every level exact.

        The font change rewraps the document, so the same scrollbar pixel value
        would now point at different text and the view appeared to scroll. The
        character at the top of the viewport is captured first and re-pinned
        after the relayout, so zooming keeps the reader in place.
        """
        anchor = self._browser.cursorForPosition(
            self._browser.viewport().rect().topLeft()
        ).position()
        self._zoom = max(-8, min(20, level))
        self._apply_preview_zoom()
        self._update_zoom_label()
        self._scroll_preview_to(anchor)

    def _scroll_preview_to(self, position: int) -> None:
        """Scroll the preview so document `position` sits at the viewport top."""
        document = self._browser.document()
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        block = cursor.block()
        offset = 0.0
        layout = block.layout()
        if layout is not None:
            line = layout.lineForTextPosition(cursor.positionInBlock())
            if line.isValid():
                offset = line.y()
        top = document.documentLayout().blockBoundingRect(block).top()
        self._browser.verticalScrollBar().setValue(max(0, round(top + offset)))

    def _apply_preview_zoom(self) -> None:
        if self._preview_base_font is None:
            return
        font = QFont(self._preview_base_font)  # QFont is mutable: copy it
        if self._zoom:
            size = font.pointSizeF() * (1.0 + 0.1 * self._zoom)
            font.setPointSizeF(max(6.0, size))
        self._browser.document().setDefaultFont(font)

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

    def _set_toc_zoom(self, level: int) -> None:
        """Outline zoom only, independent of the other panes."""
        self._toc_zoom = max(-8, min(20, level))
        if self._toc_base_font is None:
            return
        font = QFont(self._toc_base_font)  # QFont is mutable: copy it
        if self._toc_zoom:
            size = font.pointSizeF() * (1.0 + 0.1 * self._toc_zoom)
            font.setPointSizeF(max(6.0, size))
        self._toc.setFont(font)

    def _open_file(self) -> None:
        if not self._confirm_discard():
            return
        start = self._settings.value("last_dir", str(Path.home()))
        name, _ = QFileDialog.getOpenFileName(
            self, APP_NAME, start, "Markdown (*.md *.markdown *.txt);;All files (*)"
        )
        if not name:
            return
        self._navigate(Path(name))