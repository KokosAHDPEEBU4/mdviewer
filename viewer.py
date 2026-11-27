"""The viewer window: renders one markdown file with the dark theme.

Read mode shows the rendered document.  Edit mode (Ctrl+E) opens a plain-text
pane next to it with the markdown source: nothing is converted back and forth,
the source is just what is on disk.  "Конвертировать" re-renders the preview
from the edited text, "Сохранить" writes the file and refreshes the preview.
"""

from collections import namedtuple
from pathlib import Path
from html import escape
from typing import TYPE_CHECKING
import ctypes
import re
import sys

from PyQt6.QtCore import (
    QByteArray,
    QEvent,
    QFileSystemWatcher,
    QMarginsF,
    QMimeData,
    QPoint,
    QPointF,
    QRegularExpression,
    QRect,
    QSettings,
    QSize,
    QTimer,
    QUrl,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QDrag,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QMouseEvent,
    QPageLayout,
    QPainter,
    QPen,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import drafts

# mistune (+ its plugins) costs ~70 ms to import; the renderer module is loaded
# on the first render instead, so the window chrome appears that much earlier
# (invariant 36: no heavy imports on the path to the first frame)
from syncsel import SourceMap
from theme import (
    ACCENT,
    ACCENT_DIM,
    BORDER,
    CODE_BG,
    DEFAULT_THEME,
    EDITOR_HARD,
    EDITOR_MAX,
    EDITOR_MIN,
    EDITOR_PREF,
    EDITOR_RATIO,
    EDITOR_SELECTION,
    GUTTER_MIN,
    HYST,
    HL_OVERLAY_ALPHA,
    MEASURE,
    MIN_COLUMN,
    MIN_WINDOW_WIDTH,
    PREVIEW_HARD,
    PREVIEW_SELECTION,
    SCREEN_FALLBACK,
    SPLIT_HANDLE,
    SURFACE,
    TEXT_MUTED,
    TOC_MIN_WIDTH,
    TOC_WIDTH,
    TREE_MIN_WIDTH,
    TREE_WIDTH,
    app_qss,
    column_side,
    content_css,
    is_light,
    normalize,
    palette,
    retheme_tables,
)
from version import APP_NAME, APP_VERSION

if TYPE_CHECKING:
    from renderer import Heading

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


# PDF export page margin (millimetres). Generous enough that the text never
# touches the paper edge, while still leaving a wide reading column.
PDF_MARGIN_MM = 15


def _standalone_html(title: str, body: str, theme: str) -> str:
    """Wrap rendered `body` into a self-contained HTML page for `theme`.

    The theme's `content_css` is embedded, the encoding is declared and the
    title is the document's file name. Local images stay `file://` URLs (they
    are already absolute), so the page makes no network requests.
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ru">\n<head>\n'
        '<meta charset="utf-8">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>\n{content_css(theme)}\n</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


# A file save is rarely one event (editors write a temp file and rename it), so
# the external-change reload waits this long to coalesce the burst before it
# reads the file once. Short enough to feel instant, long enough to be one read.
FILE_WATCH_DEBOUNCE_MS = 200

# Unsaved edits are copied to a crash-safe draft this long after the last
# keystroke. Restarted on every change (single-shot), so it only fires once the
# typist pauses; the text survives even a hard PyQt crash.
DRAFT_AUTOSAVE_MS = 3000


# How many files the "Недавние" submenu remembers. Stored newest-first, deduped
# by resolved path, and pruned of anything that has since disappeared.
RECENT_LIMIT = 10
RECENT_KEY = "recent_files"

# Editor <-> preview pair scroll (Alt+wheel while editing). A wheel burst emits
# many steps; the timer coalesces them into one cross-pane move.
SYNC_SCROLL_MS = 70

# Zoom levels offered as the default for new tabs (the dialog's combo and the
# "масштаб по умолчанию" setting). Stored as steps of 10% on top of 100%.
DEFAULT_ZOOM_LEVELS = (-2, -1, 0, 1, 2, 5, 10)

# One fenced code card in the current render. Qt splits a `<pre>` into one block
# per line (each block carries the code background), so a run is a contiguous
# range of blocks; `start`/`end` are block numbers, `card_start` includes the
# language caption above the code, and `text` is what the copy button puts on the
# clipboard (lines joined with "\n", language caption left out).
_CodeRun = namedtuple("_CodeRun", "start end text card_start")


def zoom_percent(level: int) -> int:
    """Human-readable percentage for a zoom step (`0` -> 100)."""
    return round((1.0 + 0.1 * level) * 100)


def recent_files(settings: QSettings) -> list[str]:
    """The remembered file paths, newest first; only ones still on disk."""
    raw = settings.value(RECENT_KEY, [], type=list) or []
    return [str(item) for item in raw if Path(item).is_file()]


def remember_recent_file(path: Path, settings: QSettings) -> list[str]:
    """Move `path` to the front of the recents, dropping duplicates and stale
    entries. Shared by tab opens and in-window navigation so both paths agree.
    """
    target = str(Path(path).resolve())
    raw = settings.value(RECENT_KEY, [], type=list) or []
    recent = [str(item) for item in raw if str(item) != target and Path(item).is_file()]
    recent.insert(0, target)
    recent = recent[:RECENT_LIMIT]
    settings.setValue(RECENT_KEY, recent)
    return recent


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


def editor_width(content: int, ratio: float) -> int:
    """Editor width from the editor/(editor+preview) ratio, clamped (B3).

    `content` is the area shared by the preview and the editor (toc and handles
    already removed). Pure, so the ratio policy is testable on its own.
    """
    ratio = min(max(float(ratio), 0.05), 0.95)
    return max(EDITOR_MIN, min(EDITOR_MAX, round(ratio * max(0, int(content)))))


def editor_cap_width(ratio: float) -> int:
    """Editor width that pairs a MEASURE-wide preview with `ratio` (B2).

    Solving `e = ratio * (MEASURE + e)` gives `e = ratio/(1-ratio) * MEASURE`,
    so at the cap the editor/preview proportion is exactly `ratio`.
    """
    ratio = min(max(float(ratio), 0.05), 0.95)
    return max(EDITOR_MIN, min(EDITOR_MAX, round(ratio / (1.0 - ratio) * MEASURE)))


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


# --- tree panel helpers (pure, testable without Qt) ------------------------

FILE_ATTRIBUTE_HIDDEN = 0x2   # Windows FILE_ATTRIBUTE_HIDDEN
HIDDEN_FILE_NAMES = {"desktop.ini", "thumbs.db"}


def natural_key(name: str):
    """Sort key for file names: digit runs compare as numbers, the rest
    case-insensitively (`b2.md` before `b10.md`, кириллица through casefold).
    Each piece is a tuple so an int never compares against a str."""
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", name)
    ]


def is_hidden_file(path: Path) -> bool:
    """Windows-hidden by attribute (Q5), the shell noise pair, or a dotfile."""
    if path.name.lower() in HIDDEN_FILE_NAMES or path.name.startswith("."):
        return True
    if sys.platform == "win32":
        try:
            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        except (AttributeError, OSError):
            return False
        if attributes != -1 and attributes & FILE_ATTRIBUTE_HIDDEN:
            return True
    return False


# DwmSetWindowAttribute: the native caption is drawn light grey on Windows 10
# unless the app opts out, and Qt exposes no API for it.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20          # Windows 10 20H1+
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19      # Windows 10 1809..1909


def set_dark_title_bar(widget, enabled: bool) -> bool:
    """Ask DWM for a dark (True) or light (False) caption.

    Returns False when the OS will not have it. The same attribute is used by
    the old and new Windows builds; passing 0 restores the light caption, which
    is what the light theme wants.
    """
    if sys.platform != "win32":
        return False
    try:
        value = ctypes.c_int(1 if enabled else 0)
        for attribute in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(widget.winId()), attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result == 0:
                return True
    except (AttributeError, OSError):
        pass
    return False


def enable_dark_title_bar(widget) -> bool:
    """Backwards-compatible alias: request a dark caption."""
    return set_dark_title_bar(widget, True)


class _OverlayMixin:
    """Draws a custom overlay *after* the widget's own painting.

    Qt paints the native selection **over** `extraSelections`, so a mirrored
    highlight would sit under the pane's own selection and lose the "newest on
    top" rule. Painting it by hand in `paintEvent`, after `super()`, puts it
    genuinely on top. `overlay_painter` is `callable(widget, painter)`.
    """

    overlay_painter = None

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.overlay_painter is not None:
            painter = QPainter(self.viewport())
            self.overlay_painter(self, painter)
            painter.end()


class OverlayTextBrowser(_OverlayMixin, QTextBrowser):
    pass


class OverlayPlainTextEdit(_OverlayMixin, QPlainTextEdit):
    pass


class LineNumberArea(QWidget):
    """The gutter next to a `LineNumberedEdit`.

    It is a child widget parked in the viewport margin the editor reserves, so
    the numbers never overlap the text and the editor's own `paintEvent` (which
    `_OverlayMixin` already overrides for the selection mirror) is left alone.
    """

    def __init__(self, editor: "LineNumberedEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class LineNumberedEdit(OverlayPlainTextEdit):
    """`QPlainTextEdit` with an optional line-number gutter.

    The gutter is toggleable at runtime (`set_line_numbers`); when off the
    reserved viewport margin drops back to zero. `_OverlayMixin`'s selection
    overlay keeps working because the numbers are drawn by the gutter widget,
    not by the editor's `paintEvent`.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_numbers = True
        self._gutter_bg = CODE_BG
        self._gutter_fg = TEXT_MUTED
        self._gutter = LineNumberArea(self)
        self._gutter.setFont(self.font())  # digits follow the (zoomable) font
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width()

    def changeEvent(self, event):
        """The zoom changes the widget font via `setFont`; the gutter must
        follow, or its reserved width and drawn digits stay at the base size."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "_gutter"):
            self._gutter.setFont(self.font())
            self._update_gutter_width()
            self._gutter.update()

    def _layout_gutter(self) -> None:
        """Park the gutter next to the viewport at the current font width.

        `setViewportMargins` resizes the *viewport*, not the editor, so no
        `resizeEvent` reaches us on a font/zoom change; laying the gutter out
        here keeps its width in step with the freshly reserved margin.
        """
        area = self.contentsRect()
        self._gutter.setGeometry(
            QRect(area.left(), area.top(), self.line_number_area_width(), area.height())
        )

    def set_palette_colours(self, background: str, foreground: str) -> None:
        """Theme switch: repaint the gutter in the active palette colours."""
        self._gutter_bg = background
        self._gutter_fg = foreground
        self._gutter.update()

    def set_line_numbers(self, enabled: bool) -> None:
        self._line_numbers = bool(enabled)
        self._gutter.setVisible(self._line_numbers)
        self._update_gutter_width()
        self._update_gutter(self.viewport().rect(), 0)
        self._layout_gutter()

    def line_numbers_visible(self) -> bool:
        return self._line_numbers and self._gutter.isVisible()

    def line_number_area_width(self) -> int:
        if not self._line_numbers:
            return 0
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_args) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)
        if hasattr(self, "_gutter"):
            self._layout_gutter()

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_gutter()

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.setFont(self.font())  # draw the digits at the editor's size
        painter.fillRect(event.rect(), QColor(self._gutter_bg))
        painter.setPen(QColor(self._gutter_fg))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        offset = self.contentOffset()
        top = round(self.blockBoundingGeometry(block).translated(offset).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    self._gutter.width() - 6,
                    height,
                    Qt.AlignmentFlag.AlignRight,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1
        painter.end()


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


class ColumnHandle(QWidget):
    """A draggable boundary of the reading column.

    It lives in the preview's side margin (a child of the `QTextBrowser`, not
    of its viewport), so it never covers text or the scrollbar. The inner edge
    of the strip is the column boundary; a faint line keeps the limit visible
    all the time and brightens with a small grip while hovered. Dragging moves
    the boundary and mirrors it on the other side, so the column stays centred;
    the width is stored in QSettings by the view. Double-click restores the
    automatic policy.
    """

    WIDTH = 8  # grab strip inside the margin (the scrollbar starts right after)

    def __init__(self, view: "DocumentView", sign: int):
        super().__init__(view._browser)
        self._view = view
        self._sign = -1 if sign < 0 else 1  # -1 = left boundary, +1 = right
        self._line = QColor(BORDER)
        self._hover_line = QColor(ACCENT_DIM)
        self._hover = False
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        # a plain child widget is transparent; the line is all we paint
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def set_colours(self, line: str, hover: str) -> None:
        """Theme switch: recolour the faint line and its hover state."""
        self._line = QColor(line)
        self._hover_line = QColor(hover)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        colour = QColor(self._hover_line if self._hover else self._line)
        if not self._hover:
            colour.setAlpha(150)  # visible, but not a frame around the page
        x = self.width() - 1 if self._sign < 0 else 0
        painter.setPen(QPen(colour, 1))
        painter.drawLine(x, 0, x, self.height())
        if self._hover:
            # three dots on the boundary: the "you can drag me" grip
            centre = self.height() // 2
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            for offset in (-8, 0, 8):
                painter.drawEllipse(QPoint(x, centre + offset), 2, 2)
        painter.end()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._view._begin_column_drag(self._sign, event.globalPosition().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._view._drag_column(event.globalPosition().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._view._end_column_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._view._reset_column_width()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DocumentView(QWidget):
    SCROLL_DEAD_ZONE = 12   # px the pointer must move before autoscroll kicks in
    WHEEL_STEP = 40.0       # accumulated wheel units that make one zoom level
    CRUMB_LABEL_MAX = 40    # characters of a heading before the crumb is truncated
    CRUMB_SEGMENT_MAX = 4   # most crumbs shown; deeper paths collapse to "…"
    PATH_FOLDER_MAX = 240   # characters of the folder in the path strip before "…"
    COPY_FLASH_MS = 800     # how long the "Скопировано" state stays on the button
    CODE_COPY_LABEL = "Копировать"
    CODE_COPY_DONE = "Скопировано"
    OVERLAY_HOT_ZONE = 60   # px from the preview's top edge that summons the panel
    OVERLAY_HIDE_MS = 150   # delay before the panel goes away after the pointer leaves

    # `state_changed` fires whenever the dirty state may have moved (the tab
    # label and the window title follow it); `link_requested` asks the host
    # window to open a markdown link in a *new* tab (Ctrl/middle click).
    state_changed = pyqtSignal()
    link_requested = pyqtSignal(object)

    def __init__(self, path: Path, settings: QSettings | None = None):
        super().__init__()
        self._path = Path(path).resolve()
        self._settings = settings or QSettings()
        self._theme = normalize(self._settings.value("theme", DEFAULT_THEME))
        self._unsaved = False
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
        self._preview_width = 0      # preview width measured just before edit
        self._outline: list[Heading] = []
        self._history: list[Path] = []   # files visited before the current one
        self._selected_section = None    # outline row the user last clicked
        # pane shrink policy state (see `_relayout`): the user's wish to see the
        # outline is kept apart from the fact that it is auto-hidden on a narrow
        # window, so widening can bring it back without overriding the user
        self._toc_user_visible = True
        self._toc_auto_hidden = False
        # the edit split is a ratio, not a fixed width, so 27" and 34" look the
        # same (B3); persisted so a manual drag survives a restart
        try:
            stored_ratio = float(self._settings.value("editor_ratio", EDITOR_RATIO))
        except (TypeError, ValueError):
            stored_ratio = EDITOR_RATIO
        self._editor_ratio = min(max(stored_ratio, 0.05), 0.95)
        self._pre_edit_rect = None   # window geometry before entering edit (live)
        self._edit_resized = False   # did `_enter_edit` actually resize the window
        self._in_relayout = False
        # reading mode (F11 host): outline hidden temporarily, overlay suppressed
        self._reading_mode = False
        self._reading_toc_saved = None
        self._crumbs = None
        self._toc_filter = None
        self._toc_panel = None
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
        # editor <-> preview pair scroll (Alt+wheel while editing). One flag
        # breaks the feedback loop (setting the other bar re-emits valueChanged),
        # the timer throttles a burst of steps, and `_sync_source` is the pane
        # that started the pending sync.
        self._syncing_scroll = False
        self._sync_source = None
        self._sync_count = 0          # applied syncs (diagnostics / tests)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(SYNC_SCROLL_MS)
        self._sync_timer.timeout.connect(self._sync_scroll_now)
        # middle-click autoscroll: one pane at a time, tracked per pane
        self._autoscroll_pane = None
        self._autoscroll_origin = QPoint(0, 0)
        self._autoscroll_offset = QPoint(0, 0)
        self._scroll_indicator = None
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)

        # external file changes: one watcher per view (one view -> one file).
        # `_watch` is the path currently armed; it follows `_path` on navigation.
        # The watcher is a child, so it is torn down with the view.
        self._watch: Path | None = None
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_watch_event)
        # editors save via temp+rename, so one save arrives as a burst of events;
        # a single-shot timer coalesces them before any reload work is done
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(FILE_WATCH_DEBOUNCE_MS)
        self._reload_timer.timeout.connect(self._on_file_changed)

        # crash-safe draft: one single-shot timer, restarted on every edit, so
        # the pending text reaches disk once the typist pauses (see drafts.py)
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(DRAFT_AUTOSAVE_MS)
        self._draft_timer.timeout.connect(self._autosave_draft)

        # the window title and geometry belong to MainWindow (see below)

        self._browser = OverlayTextBrowser()
        self._browser.overlay_painter = self._paint_selection_overlay
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        # Let every click come through `anchorClicked`: Qt's own handling would
        # either `setSource` the raw markdown (no styling) or hand the file to
        # another program. We navigate inside the window instead.
        self._browser.setOpenLinks(False)
        self._browser.setOpenExternalLinks(False)
        self._browser.anchorClicked.connect(self._open_link)
        # a file dropped over the preview belongs to the window (Q18: new tab);
        # the browser's own URL-drop handling would swallow the drag instead
        self._browser.setAcceptDrops(False)
        self._browser.viewport().setAcceptDrops(False)
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
        self._browser.verticalScrollBar().valueChanged.connect(self._sync_crumbs)
        # the copy strip follows the selection and has to survive scrolls
        self._browser.verticalScrollBar().valueChanged.connect(self._reposition_copy_popup)
        # the code-block copy button is glued to its card, so it follows too
        self._browser.verticalScrollBar().valueChanged.connect(self._reposition_code_copy)
        self._browser.selectionChanged.connect(self._highlight_editor_from_preview)
        self._browser.selectionChanged.connect(self._update_copy_popup)
        self._preview_base_font = QFont(self._browser.document().defaultFont())

        # The reading column is centred by viewport margins; its boundaries are
        # two drag strips living in those side margins. `_column_width` is the
        # user's width (0 = the automatic MEASURE policy of theme.side_margin).
        try:
            self._column_width = max(
                0, int(self._settings.value("column_width", 0) or 0)
            )
        except (TypeError, ValueError):
            self._column_width = 0
        self._column_drag = None  # (sign, start_x, start_width, start_pref)
        self._column_anchor = None  # preview char to pin across a width drag
        self._column_handles = (ColumnHandle(self, -1), ColumnHandle(self, 1))
        self._refresh_column_handles()

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        # a wide handle is much easier to catch with the mouse than the ~5 px default
        self._splitter.setHandleWidth(SPLIT_HANDLE)
        # The outline is built *first*: a filter event can reach `eventFilter`
        # while the preview is being assembled, and `_pane_for` reads `_toc`.
        self._splitter.addWidget(self._build_outline())

        # Breadcrumbs and the file path share one thin strip above the preview.
        # The browser is wrapped (not replaced): every `self._browser` lookup
        # keeps working, and the splitter still owns three children (outline /
        # preview / editor).
        self._preview_panel = QWidget()
        preview_layout = QVBoxLayout(self._preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        self._path_strip = QWidget()
        self._path_strip.setObjectName("pathStrip")
        strip_layout = QHBoxLayout(self._path_strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(0)
        self._crumbs = QLabel()
        self._crumbs.setObjectName("breadcrumbs")
        self._crumbs.setTextFormat(Qt.TextFormat.RichText)
        self._crumbs.setOpenExternalLinks(False)
        self._crumbs.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        # the crumbs fill the strip left of the path; the strip must never
        # widen the preview pane
        self._crumbs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._crumbs.setWordWrap(False)
        self._crumbs.linkActivated.connect(self._crumb_clicked)
        self._crumbs.setVisible(False)
        strip_layout.addWidget(self._crumbs)
        # the path itself: click anywhere on it -> Explorer in the file's folder
        self._path_label = QLabel()
        self._path_label.setObjectName("pathBar")
        self._path_label.setTextFormat(Qt.TextFormat.RichText)
        self._path_label.setOpenExternalLinks(False)
        self._path_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._path_label.setWordWrap(False)
        self._path_label.linkActivated.connect(self._reveal_folder)
        self._path_label.setVisible(False)
        strip_layout.addWidget(
            self._path_label, 0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        preview_layout.addWidget(self._path_strip)
        preview_layout.addWidget(self._browser, 1)
        self._splitter.addWidget(self._preview_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        # the divider changes the preview width without resizing the window, so
        # no resizeEvent arrives and the insets would keep their old value
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        # the splitter resizes a step after the window, so its own Resize event
        # (which carries the final size) drives the pane layout
        self._splitter.installEventFilter(self)
        # DocumentView is the central widget now, so the splitter needs a layout
        # to fill it (previously the splitter was the window's central widget).
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._splitter)

        self._edit_btn = None
        self._back_btn = None
        self._toc_btn = None
        self._overlay = None
        self._overlay_pin = False  # find/replace holds the panel open
        self._overlay_hide = QTimer(self)
        self._overlay_hide.setSingleShot(True)
        self._overlay_hide.setInterval(self.OVERLAY_HIDE_MS)
        self._overlay_hide.timeout.connect(self._maybe_hide_overlay)
        self._find_bar = None
        # the copy strip that follows a preview selection (built on first use)
        self._copy_popup = None
        self._copy_plain_btn = None
        self._copy_md_btn = None
        self._copy_stale = False
        self._copy_offscreen = False  # hidden because the selection left the view
        # the copy button that appears over a fenced code block on hover
        self._code_runs = []           # `_CodeRun` cards of the current render
        self._code_run_by_block = {}   # block number -> `_CodeRun`
        self._code_copy_btn = None     # built lazily, child of the viewport
        self._code_copy_run = None     # card the visible button belongs to
        self._code_copy_flash = QTimer(self)
        self._code_copy_flash.setSingleShot(True)
        self._code_copy_flash.setInterval(self.COPY_FLASH_MS)
        self._code_copy_flash.timeout.connect(self._reset_code_copy_label)
        # leaving the preview towards the button itself sends a Leave first; the
        # delayed check keeps the button alive under the pointer
        self._code_copy_hide = QTimer(self)
        self._code_copy_hide.setSingleShot(True)
        self._code_copy_hide.setInterval(150)
        self._code_copy_hide.timeout.connect(self._maybe_hide_code_copy)

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
        self._update_path_bar()  # the strip shows the file just loaded
        self._watch_current()  # the watch follows the file that is now shown
        self._maybe_restore_draft()  # offer back a crash-safe draft, if any
        return True

    def _render(self, text: str) -> None:
        """Re-render the preview. Called only on load/convert/save, never on
        resize (that would cost hundreds of milliseconds per step)."""
        from renderer import render_json, render_with_outline  # lazy: mistune cost

        # setHtml rebuilds the document, so any stored highlight cursor would
        # dangle; drop the layers first
        self._clear_highlights()
        self._hide_copy_popup()  # the selection the strip refers to is gone
        self._hide_code_copy()  # the code cards are about to be rebuilt
        self._rendered = _normalized(text)
        # the stylesheet has to be in place before the HTML: Qt matches CSS while
        # importing, not afterwards
        self._browser.document().setDefaultStyleSheet(content_css(self._theme))
        if self._path.suffix.lower() == ".json":
            html, self._outline = render_json(text)
        else:
            html, self._outline = render_with_outline(text, self._path.parent)
        # renderer bakes the dark table colours into `bgcolor`; recolour them
        # for the light theme (a no-op for the default dark palette)
        html = retheme_tables(html, self._theme)
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
        self._scan_code_blocks()  # card ranges for the hover copy button
        self._sync_crumbs()

    def _render_busy(self, text: str) -> None:
        """Same, with a wait cursor: a big file takes ~0.5 s to render."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._render(text)
        finally:
            QApplication.restoreOverrideCursor()

    # -- export ------------------------------------------------------------

    def export_html(self, dest: Path) -> bool:
        """Write the current document as a standalone HTML file.

        `self._map_source` is the markdown the preview currently shows (it is
        refreshed only on load/convert/save, invariant 12), so the export
        matches the preview even when the editor holds an un-converted draft.
        Local images stay `file://` URLs; the theme's CSS is embedded. Nothing
        is read from the network.
        """
        source = self._map_source if self._map_source is not None else self._text
        from renderer import render_json, render_with_outline  # lazy: mistune cost

        if self._path.suffix.lower() == ".json":
            body, _ = render_json(source)
        else:
            body, _ = render_with_outline(source, self._path.parent)
        body = retheme_tables(body, self._theme)
        page = _standalone_html(self._path.name, body, self._theme)
        try:
            Path(dest).write_text(page, encoding="utf-8")
        except OSError as exc:
            self._export_error(exc)
            return False
        return True

    def export_pdf(self, dest: Path) -> bool:
        """Print the preview's `QTextDocument` to a PDF file via `QPrinter`.

        A clone is printed so the on-screen document is never repaginated, and
        explicit margins keep the text off the paper edge. `PdfFormat` writes a
        plain file, so no printer (or dialog) is required — which also makes it
        safe to run from the automated checks.
        """
        from PyQt6.QtPrintSupport import QPrinter  # lazy: only for an export

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(dest))
        printer.setPageMargins(
            QMarginsF(PDF_MARGIN_MM, PDF_MARGIN_MM, PDF_MARGIN_MM, PDF_MARGIN_MM),
            QPageLayout.Unit.Millimeter,
        )
        document = self._browser.document().clone()
        try:
            document.print(printer)
        except Exception as exc:  # Qt can raise on a broken printer setup
            self._export_error(exc)
            return False
        finally:
            document.deleteLater()
        if not Path(dest).is_file() or Path(dest).stat().st_size == 0:
            self._export_error("файл не создан")
            return False
        return True

    def _export_error(self, exc) -> None:
        QMessageBox.warning(self, "Экспорт", f"Не удалось сохранить файл:\n{exc}")

    def export_via_dialog(self, kind: str) -> bool:
        """Ask for a destination, then export `kind` ('html' or 'pdf').

        Returns True once a file is written. Cancelling the dialog writes
        nothing and returns False; a write error is reported and also returns
        False. The default name is `<имя файла>.<расширение>`.
        """
        suffix = "html" if kind == "html" else "pdf"
        title = "Экспорт в HTML" if suffix == "html" else "Экспорт в PDF"
        caption = "HTML (*.html)" if suffix == "html" else "PDF (*.pdf)"
        default = self._path.with_suffix("." + suffix)
        name, _ = QFileDialog.getSaveFileName(self, title, str(default), caption)
        if not name:
            return False
        dest = Path(name)
        if dest.suffix.lower() != "." + suffix:
            try:
                dest = dest.with_suffix("." + suffix)
            except ValueError:
                pass
        if suffix == "html":
            return self.export_html(dest)
        return self.export_pdf(dest)

    # -- theme -------------------------------------------------------------

    def _pal(self) -> dict:
        """Colour tokens of the theme this view is showing."""
        return palette(self._theme)

    def _refresh_theme_chrome(self) -> None:
        """Re-tint the bits painted by hand (inline label styles, gutter)."""
        p = self._pal()
        caption = getattr(self, "_editor_caption", None)
        if caption is not None:
            caption.setStyleSheet(f"color: {p['TEXT_MUTED']}; font-size: 11px;")
        status = getattr(self, "_find_status", None)
        if status is not None:
            status.setStyleSheet(f"color: {p['TEXT_MUTED']};")
        editor = getattr(self, "_editor", None)
        if editor is not None and hasattr(editor, "set_palette_colours"):
            editor.set_palette_colours(p["CODE_BG"], p["TEXT_MUTED"])
        self._refresh_column_handles()

    def _refresh_column_handles(self) -> None:
        p = self._pal()
        for handle in getattr(self, "_column_handles", ()) or ():
            handle.set_colours(p["BORDER"], p["ACCENT_DIM"])

    def _apply_theme(self, name: str) -> None:
        """Switch this view to `name`, re-rendering and keeping the position.

        The application stylesheet is set by the window; here the document
        stylesheet, the hand-painted chrome and the rendered table colours are
        brought in line, and the character at the top of the viewport is pinned
        across the re-render (a theme swap can rewrap the document).
        """
        name = normalize(name)
        self._theme = name
        self._refresh_theme_chrome()
        anchor = self._preview_top_cursor().position()
        self._render(self._text)
        self._scroll_preview_to(anchor)

    # -- external changes (`QFileSystemWatcher`) ---------------------------

    def _watch_current(self) -> None:
        """Point the filesystem watch at the file shown right now.

        Called after every successful `load()`, so navigation (`_navigate`,
        `_go_back`, link clicks) and F5 re-arm the watch on the new path.
        """
        self._watch = self._path
        self._rewatch()

    def _unwatch(self) -> None:
        """Drop every path from the watcher (the view still shows the file)."""
        watched = self._watcher.files()
        if watched:
            self._watcher.removePaths(watched)

    def _rewatch(self) -> None:
        """(Re)install the watch for `self._watch`.

        On Windows the watch is often dropped when the file is atomically
        replaced (temp+rename), so the path is removed and added again after
        every notification. A path that no longer exists is simply left
        unwatched; the shown document is never touched here.
        """
        self._unwatch()
        if self._watch is not None and self._watch.is_file():
            self._watcher.addPath(str(self._watch))

    def _on_watch_event(self, _path: str) -> None:
        """A raw filesystem notification: re-arm and debounce the reload."""
        self._rewatch()
        self._reload_timer.start()

    def _on_file_changed(self) -> None:
        """React to the watched file changing on disk (after the debounce).

        With no unsaved edits the file is re-read and repainted automatically,
        keeping the reading position. With unsaved edits the user is asked
        first; "Нет" leaves everything as it is. A missing or unreadable file
        is ignored (only the watch is adjusted) so a delete/rename never
        disturbs what is on screen.
        """
        self._rewatch()
        if self._watch != self._path or not self._path.is_file():
            return
        try:
            text, encoding, newline = read_document(self._path)
        except OSError:
            return
        if _normalized(text) == _normalized(self._text):
            return  # a spurious event: the contents on disk did not change
        if self._is_unsaved():
            answer = QMessageBox.question(
                self,
                APP_NAME,
                "Файл изменён на диске. Перезагрузить и потерять ваши правки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._reload_from_disk(text, encoding, newline)

    def _reload_from_disk(
        self,
        text: str | None = None,
        encoding: str | None = None,
        newline: str | None = None,
    ) -> bool:
        """Re-read the current file and repaint, keeping the reading position.

        Unlike `load()` this never asks to discard edits: the caller has already
        decided (silently when clean, after the prompt when dirty). Returns
        False when the file is gone or cannot be read.
        """
        if text is None:
            try:
                text, encoding, newline = read_document(self._path)
            except OSError:
                return False
        # remember the character on the top edge, exactly like a zoom or `load`:
        # the document is about to be rebuilt, so a raw scroll pixel would point
        # at different text afterwards
        anchor = self._preview_top_cursor().position()
        self._text = text
        self._encoding = encoding or self._encoding
        self._newline = newline or self._newline
        self._clear_highlights()
        self._selected_section = None  # the outline is rebuilt for this content
        self._render(text)
        self._baseline = _normalized(text)
        if self._editor is not None:
            self._editor.setPlainText(text)
            self._editor.document().setModified(False)
            self._map_dirty = False  # matches the render that just happened
        self._update_state()
        self._scroll_preview_to(anchor)
        return True

    def _preview_top_cursor(self):
        """Cursor on the top visual line, sampled inside the line.

        The old `viewport().rect().topLeft()` point (x=0) falls to the *left*
        of an indented line — code/JSON lines start 14 px in — and Qt then
        hit-tests the line above, so section/anchor bookkeeping slipped one
        line back. The middle of the viewport is always on the top line.
        """
        view = self._browser.viewport().rect()
        # y=0 itself belongs to the line *above* (its bottom edge): one pixel in
        return self._browser.cursorForPosition(
            QPoint(max(1, view.width() // 2), view.top() + 1)
        )

    def _set_margins(self) -> None:
        """Keep the text column at the chosen (or automatic) width.

        Done with viewport margins rather than CSS: a resize then costs a
        geometry update instead of re-parsing the whole document. The same
        margins host the two column handles, so they are re-parked here too
        (their position depends on the width even when the inset does not).
        """
        side = column_side(self._browser.contentsRect().width(), self._column_width)
        if side != self._side:
            self._side = side
            self._browser.setViewportMargins(side, 0, side, 0)
        self._place_column_handles()

    def _place_column_handles(self) -> None:
        """Park the boundary strips in the side margins, inside the column edges."""
        handles = getattr(self, "_column_handles", None)
        if not handles:
            return
        browser = self._browser
        grab = ColumnHandle.WIDTH
        side = max(0, self._side)
        left, right = handles
        left.setGeometry(max(0, side - grab), 0, grab, browser.height())
        right.setGeometry(max(0, browser.width() - side), 0, grab, browser.height())
        visible = not self._reading_mode and side >= grab
        left.setVisible(visible)
        right.setVisible(visible)
        if visible:
            left.raise_()
            right.raise_()

    def _begin_column_drag(self, sign: int, global_x: float) -> None:
        """Start dragging a boundary: remember the span in global coordinates.

        Global coordinates keep the drag steady while the inset (and therefore
        the handle itself) moves under the pointer on every step.
        """
        # the column includes the side insets; the viewport additionally loses
        # the scrollbar width, which must not be shaved off on every drag
        column = self._browser.contentsRect().width() - 2 * max(0, self._side)
        self._column_drag = (sign, global_x, column, self._column_width)
        # a width change rewraps the document; pin the character the reader is
        # looking at so the drag does not scroll them somewhere else
        self._column_anchor = self._preview_top_cursor().position()
        self._hide_code_copy()
        self._hide_copy_popup()
        self._hide_overlay()

    def _drag_column(self, global_x: float) -> None:
        if self._column_drag is None:
            return
        sign, start_x, start_width, _ = self._column_drag
        # the other boundary mirrors this one, so the column grows by two
        # pixels per dragged pixel and the edge follows the pointer exactly
        self._set_column_width(start_width + 2 * sign * (global_x - start_x))

    def _end_column_drag(self) -> None:
        drag = self._column_drag
        self._column_drag = None
        if drag is None or self._column_width == drag[3]:
            self._column_anchor = None  # a click, not a drag: nothing moved
            return
        self._settings.setValue("column_width", int(self._column_width))
        self._broadcast_column_width()
        # the layout settles on the next event loop turn, then re-pin the reader
        QTimer.singleShot(0, self._restore_column_anchor)

    def _restore_column_anchor(self) -> None:
        anchor = self._column_anchor
        self._column_anchor = None
        if anchor is not None:
            self._scroll_preview_to(anchor)

    def _set_column_width(self, width: float) -> None:
        """Clamp a dragged width to the window and to MIN_COLUMN, then apply it."""
        limit = max(1, self._browser.contentsRect().width() - GUTTER_MIN * 2)
        if limit < MIN_COLUMN:
            target = limit
        else:
            target = max(MIN_COLUMN, min(int(round(width)), limit))
        self._column_width = target
        self._set_margins()

    def _reset_column_width(self) -> None:
        """Double-click on a boundary: back to the automatic MEASURE policy."""
        anchor = self._preview_top_cursor().position()
        self._column_width = 0
        self._column_drag = None
        self._column_anchor = anchor
        self._settings.setValue("column_width", 0)
        self._set_margins()
        self._broadcast_column_width()
        QTimer.singleShot(0, self._restore_column_anchor)

    def _apply_column_width(self, width: int) -> None:
        self._column_width = max(0, int(width))
        self._set_margins()

    def _broadcast_column_width(self) -> None:
        """A width dragged in one tab applies to every open tab and window."""
        for window in _live_windows():
            for index in range(window._tabs.count()):
                view = window._tabs.widget(index)
                if view is not None and view is not self:
                    view._apply_column_width(self._column_width)

    def _on_splitter_moved(self, *_args) -> None:
        """A user drag: remember the pane widths as the new preferences.

        `setSizes` does not emit `splitterMoved`, so anything seen here came from
        the mouse; the `_in_relayout` guard is belt-and-braces. Dragging the
        editor boundary rewrites the saved `editor_ratio` (B3), so the same
        proportion is reused on any window width.
        """
        self._set_margins()
        if self._in_relayout:
            return
        sizes = self._splitter.sizes()
        if not sizes:
            return
        if self._toc_panel.isVisible():
            self._settings.setValue("toc_width", max(TOC_MIN_WIDTH, sizes[0]))
        if self._editing() and self._panel is not None and self._panel.isVisible():
            # sizes index every child (hidden included): [toc, preview, editor].
            # Remember the share as a ratio, not px, so it carries between
            # monitors of different widths (B3).
            editor = sizes[-1]
            preview = sizes[-2] if len(sizes) >= 2 else 0
            span = editor + preview
            if span > 0:
                ratio = min(max(editor / span, 0.05), 0.95)
                self._editor_ratio = ratio
                self._settings.setValue("editor_ratio", ratio)

    # -- table of contents -------------------------------------------------

    def _build_outline(self) -> QWidget:
        """The outline pane: a filter row on top of the heading list."""
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

        self._toc_filter = QLineEdit()
        self._toc_filter.setObjectName("tocFilter")
        self._toc_filter.setPlaceholderText("Фильтр…")
        self._toc_filter.textChanged.connect(self._apply_toc_filter)
        self._toc_filter.installEventFilter(self)

        panel = QWidget()
        panel.setObjectName("tocPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toc_filter)
        layout.addWidget(self._toc, 1)

        self._toc_panel = panel
        panel.setMinimumWidth(TOC_MIN_WIDTH)
        panel.setVisible(False)  # shown once a document has headings
        return panel

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
        # a rebuilt outline re-applies whatever is in the filter field
        self._apply_toc_filter(self._toc_filter.text() if self._toc_filter else "")
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
        if self._toc_panel.isVisible():
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
        self._sync_crumbs()

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
        cursor = self._preview_top_cursor()
        index = self._anchored_index(cursor.block(), cursor.position())
        return index if index is not None else 0

    @staticmethod
    def _anchored_index(block, position=None):
        """Outline index of the nearest `md-hN` anchor at or above `position`.

        The anchor usually sits a few characters *after* the top-left cursor:
        a JSON key line starts with indentation and a heading can have a prefix
        fragment. Anchors on the cursor's own visual line therefore count even
        when they are after `position`; anchors further down are not. Earlier
        blocks contribute their last anchor (ordinary headings have one).
        """
        limit = position if position is not None else 10**12
        line_start = None
        line_end = None
        first = True
        while block.isValid():
            if first and position is not None:
                layout = block.layout()
                offset = max(0, position - block.position())
                line = layout.lineForTextPosition(offset) if layout is not None else None
                if line.isValid():
                    line_start = block.position() + line.textStart()
                    line_end = line_start + line.textLength()
            found = None
            fragment = block.begin()
            while fragment != block.end():
                piece = fragment.fragment()
                if piece.isValid():
                    for name in piece.charFormat().anchorNames():
                        if not name.startswith("md-h"):
                            continue
                        same_line = (
                            line_start is not None
                            and line_start <= piece.position() <= line_end
                        )
                        if piece.position() <= limit or same_line:
                            found = int(name[4:])  # last one in document order
                fragment += 1
            if found is not None:
                return found
            limit = 10**12  # earlier blocks: any anchor is above the viewport
            line_start = line_end = None
            first = False
            block = block.previous()
        return None

    def _current_section_label(self) -> str | None:
        """Label of the section currently at the top of the preview."""
        if not self._outline:
            return None
        return self._outline[self._current_section_index()].label

    def _sync_outline(self) -> None:
        """Highlight the section that owns the top of the viewport."""
        if self._toc_syncing or not self._toc_panel.isVisible() or not self._outline:
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

    # -- outline filter ----------------------------------------------------

    def _apply_toc_filter(self, text: str = "") -> None:
        """Hide outline rows that do not match the filter (case-insensitive).

        Typing is a request to see the outline: if it was hidden it is shown
        again so the matches are actually visible.
        """
        if self._toc is None or self._toc_filter is None:
            return
        needle = (text or "").strip().lower()
        if needle and self._toc_panel is not None and not self._toc_panel.isVisible():
            self._set_outline_visible(True)
        for row in range(self._toc.count()):
            item = self._toc.item(row)
            if item is not None:
                item.setHidden(bool(needle) and needle not in item.text().lower())

    # -- breadcrumbs -------------------------------------------------------

    def _crumb_path(self) -> list[int]:
        """Outline rows from the top ancestor down to the current section.

        Ancestors are the nearest earlier headings with a strictly smaller
        level, so `## A` under `# T` under `### S` reads as one path.
        """
        if not self._outline:
            return []
        index = self._current_section_index()
        if not 0 <= index < len(self._outline):
            return []
        path = [index]
        level = self._outline[index].level
        for row in range(index - 1, -1, -1):
            if self._outline[row].level < level:
                path.append(row)
                level = self._outline[row].level
                if level <= 1:
                    break
        path.reverse()
        return path

    @staticmethod
    def _crumb_label(label: str) -> str:
        """HTML-escaped heading text, truncated so one crumb cannot get huge."""
        text = (label or "").strip()
        if len(text) > DocumentView.CRUMB_LABEL_MAX:
            text = text[: DocumentView.CRUMB_LABEL_MAX - 1].rstrip() + "…"
        return escape(text)

    def _crumb_clicked(self, href: str) -> None:
        """Jump to the heading a crumb points at (its outline row index)."""
        try:
            row = int(href)
        except (TypeError, ValueError):
            return
        if not 0 <= row < self._toc.count():
            return
        item = self._toc.item(row)
        if item is not None:
            self._outline_clicked(item)

    def _sync_crumbs(self) -> None:
        """Render the heading path of the current section above the preview.

        Shown only when there is more than one segment: a bare top heading adds
        no navigation value. Ancestors are links (row index as href), the last
        segment is the accent-coloured current heading.
        """
        if self._crumbs is None:
            return
        path = self._crumb_path()
        if len(path) < 2:
            self._crumbs.setVisible(False)
            return
        if len(path) > self.CRUMB_SEGMENT_MAX:
            path = path[-self.CRUMB_SEGMENT_MAX:]
            trimmed = True
        else:
            trimmed = False
        p = self._pal()
        muted, accent, accent_dim = p["TEXT_MUTED"], p["ACCENT"], p["ACCENT_DIM"]
        parts = []
        if trimmed:
            parts.append(f'<span style="color:{muted}">…</span>')
        last = len(path) - 1
        for position, row in enumerate(path):
            label = self._crumb_label(self._outline[row].label)
            if position == last:
                parts.append(f'<span style="color:{accent}">{label}</span>')
            else:
                parts.append(
                    f'<a href="{row}" style="color:{accent_dim}; text-decoration:none">{label}</a>'
                )
        separator = f'<span style="color:{muted}"> › </span>'
        self._crumbs.setText(separator.join(parts))
        self._crumbs.setVisible(True)

    # -- file path strip ----------------------------------------------------

    def _update_path_bar(self) -> None:
        """Show the loaded file's path above the preview, folder + file name.

        The whole strip is one link: a click opens the containing folder in
        the system file manager (the tab-menu action does the same). The
        folder side is truncated from the left, so a deeply nested file
        cannot push the strip wider than the pane.
        """
        if self._path_label is None:
            return
        p = self._pal()
        muted, accent = p["TEXT_MUTED"], p["ACCENT"]
        folder = self._short_folder(str(self._path.parent))
        self._path_label.setText(
            f'<a href="folder" style="color:{muted}; text-decoration:none">'
            f"{escape(folder)}\\</a>"
            f'<a href="folder" style="color:{accent}; text-decoration:none">'
            f"{escape(self._path.name)}</a>"
        )
        self._path_label.setToolTip(str(self._path))
        self._path_label.setVisible(True)

    @classmethod
    def _short_folder(cls, folder: str) -> str:
        """Folder path clipped from the left once it exceeds `PATH_FOLDER_MAX`."""
        text = folder.rstrip("\\/")
        if len(text) > cls.PATH_FOLDER_MAX:
            text = "…" + text[-(cls.PATH_FOLDER_MAX - 1):]
        return text

    def _reveal_folder(self, _href: str = "") -> None:
        """Open the containing folder in the file manager (local paths only)."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.parent)))

    # -- reading mode (driven by the host window's F11) --------------------

    def _set_reading_mode(self, active: bool) -> None:
        """Temporarily hide the outline and the hover chrome for fullscreen.

        The user's `toc_visible` preference is saved and put back on exit; it is
        never written to QSettings, because reading mode is ephemeral. In edit
        mode the panes are left alone: the window may still go fullscreen.
        """
        self._reading_mode = bool(active)
        if self._reading_mode:
            if not self._editing():
                self._reading_toc_saved = self._toc_user_visible
                self._toc_user_visible = False
                self._toc_auto_hidden = False
            if self._overlay is not None:
                self._hide_overlay(force=True)
            self._hide_copy_popup()  # reading mode shows text, no chrome
            self._hide_code_copy()
            self._relayout()
        elif self._reading_toc_saved is not None:
            self._toc_user_visible = self._reading_toc_saved
            self._reading_toc_saved = None
            self._relayout()
        self._place_column_handles()  # no grab strips over the fullscreen text
        self._sync_crumbs()

    def _open_link(self, url: QUrl, new_tab: bool | None = None) -> None:
        """Route a link click: markdown files open in this window, everything
        else goes to the system.

        `setOpenLinks(False)` means this is the only place a click is handled,
        so a linked ``.md`` is rendered with the full theme (Qt's fallback would
        show it as plain text) and the visit is remembered for "Назад".

        Browser semantics: a plain click navigates *this* tab (history intact),
        while Ctrl+click (or a middle click, handled in `eventFilter`) asks the
        host window for a new tab. `anchorClicked` carries no modifier, so it is
        read from `QApplication.keyboardModifiers()` at handling time.
        """
        if new_tab is None:
            new_tab = bool(
                QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
            )
        target = self._link_target(url)
        if target is not None:
            if target == self._path:
                if url.hasFragment():  # an in-document anchor
                    self._browser.scrollToAnchor(url.fragment())
            elif not target.is_file():
                return  # a broken local link: nothing sensible to open
            elif self._viewable(target):
                if new_tab:
                    self.link_requested.emit(target)
                    return
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
        return path.suffix.lower() in {".md", ".markdown", ".txt", ".json"}

    def _middle_click_link(self, viewport_point: QPoint) -> bool:
        """Open the link under a middle click in a new tab. False when none.

        `anchorClicked` is not emitted for the middle button, so the anchor is
        looked up directly and resolved against the document's base URL (which
        `_render` points at the current file).
        """
        anchor = self._browser.anchorAt(viewport_point)
        if not anchor:
            return False
        url = self._browser.document().baseUrl().resolved(QUrl(anchor))
        self._open_link(url, new_tab=True)
        return True

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
        self._update_window_title(False)
        if self.load():
            remember_recent_file(self._path, self._settings)
            return True
        self._path = previous
        self._update_window_title(False)
        return False

    def _update_nav(self) -> None:
        if self._back_btn is not None:
            self._back_btn.setEnabled(bool(self._history))

    # -- edit mode --------------------------------------------------------

    def _build_edit_button(self) -> None:
        self._edit_btn = QPushButton("Редактировать")
        self._edit_btn.clicked.connect(self._toggle_edit)

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
        self._editor_caption = caption
        caption.setStyleSheet(f"color: {self._pal()['TEXT_MUTED']}; font-size: 11px;")
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
        self._editor_zoom_label.clicked.connect(self._toggle_editor_zoom_label)
        self._editor_zoom_in = QToolButton()
        self._editor_zoom_in.setText("+")
        self._editor_zoom_in.clicked.connect(lambda: self._set_editor_zoom(self._editor_zoom + 1))
        # Editor options (wrap / line numbers) live in the single
        # "Настройки" dialog now (Ctrl+, or the tab context menu); the header
        # keeps only the pane's own zoom controls.
        for widget in (
            self._editor_zoom_out,
            self._editor_zoom_label,
            self._editor_zoom_in,
        ):
            header.addWidget(widget)
        layout.addLayout(header)

        self._editor = LineNumberedEdit()
        self._editor.overlay_painter = self._paint_selection_overlay
        self._editor.setMinimumWidth(EDITOR_HARD)  # hard floor, see theme metrics
        self._editor.setPlaceholderText("Исходный Markdown — как в блокноте")
        self._editor.textChanged.connect(self._update_state)
        self._editor.textChanged.connect(self._mark_map_dirty)
        self._editor.installEventFilter(self)
        self._editor.viewport().installEventFilter(self)  # wheel lands on the viewport
        self._editor.viewport().setMouseTracking(True)  # autoscroll follows the pointer
        self._editor.selectionChanged.connect(self._highlight_preview_from_editor)
        self._editor_base_font = self._editor.font()
        self._apply_editor_options()
        self._refresh_theme_chrome()  # gutter matches the active palette
        layout.addWidget(self._editor, 1)

        self._panel = panel
        # the header row of buttons wants ~450 px; without an explicit minimum
        # the whole editor pane could not shrink below that and QSplitter would
        # steal from the preview instead (see the shrink policy in `_relayout`)
        panel.setMinimumWidth(EDITOR_HARD)
        self._splitter.addWidget(panel)
        self._splitter.setStretchFactor(1, 1)
        panel.setVisible(False)  # addWidget reveals the child; edit mode owns it

    def _editor_wrap(self) -> bool:
        """Whether long source lines wrap (default on: markdown reads better)."""
        return self._settings.value("editor_wrap", True, type=bool)

    def _editor_line_numbers(self) -> bool:
        """Whether the source gutter is shown (off by default: less chrome)."""
        return self._settings.value("editor_line_numbers", False, type=bool)

    def _apply_editor_options(self) -> None:
        """Push the saved wrap/line-number choices onto the editor."""
        if self._editor is None:
            return
        self._editor.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if self._editor_wrap()
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        if hasattr(self._editor, "set_line_numbers"):
            self._editor.set_line_numbers(self._editor_line_numbers())

    def _set_editor_wrap(self, enabled: bool) -> None:
        """Wrap toggle from the header menu: persist and apply at once."""
        self._settings.setValue("editor_wrap", bool(enabled))
        if self._editor is not None:
            self._editor.setLineWrapMode(
                QPlainTextEdit.LineWrapMode.WidgetWidth
                if enabled
                else QPlainTextEdit.LineWrapMode.NoWrap
            )

    def _set_editor_line_numbers(self, enabled: bool) -> None:
        """Line-number toggle from the header menu: persist and apply at once."""
        self._settings.setValue("editor_line_numbers", bool(enabled))
        if self._editor is not None and hasattr(self._editor, "set_line_numbers"):
            self._editor.set_line_numbers(bool(enabled))

    def _apply_settings(self, values: dict) -> None:
        """Push a settings-dialog payload onto this view's editor.

        Wrap / line numbers are applied live to an editor that already exists and
        persisted; the theme and the default zoom belong to the window
        (`MainWindow._apply_settings`). Pair scroll is not a setting: it is
        driven on demand with Alt+wheel.
        """
        if "editor_wrap" in values:
            self._set_editor_wrap(bool(values["editor_wrap"]))
        if "editor_line_numbers" in values:
            self._set_editor_line_numbers(bool(values["editor_line_numbers"]))

    def _toggle_edit(self) -> None:
        if self._editing():
            self._leave_edit()
        else:
            self._enter_edit()

    def _editing(self) -> bool:
        return self._edit_mode

    def _enter_edit(self) -> None:
        self._ensure_overlay()
        # drop any pending pair scroll from a previous Alt+wheel gesture
        self._sync_timer.stop()
        self._sync_source = None
        self._hide_copy_popup()  # the panes are about to move under it
        self._hide_code_copy()
        # the preview column shrinks a little when the editor opens (the window
        # may be capped by the screen), which rewraps the whole document; pin the
        # character at the top so the reader is not thrown to another section
        self._preview_anchor = self._preview_top_cursor().position()
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

        self._resize_for_edit()
        self._relayout()
        QTimer.singleShot(0, self._relayout)  # once the layout knows the child

        self._edit_btn.setText("Выйти из правки")
        self._editor.setFocus()
        # open the source at the same zoom the preview is at right now
        self._set_editor_zoom(self._zoom)
        self._move_editor_to_preview()
        self._update_hits(self._find_edit.text())  # search now targets the editor
        self._highlight_editor_from_preview()  # keep an existing preview selection in view
        if self._overlay is not None and self._overlay.isVisible():
            self._place_overlay()  # over the (now narrower) preview at once
        self._update_state()

    def _visible_paragraph(self) -> str:
        """Rendered text of the paragraph at the top of the preview viewport."""
        return self._preview_top_cursor().block().text().strip()

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
        p = self._pal()
        colour = p["PREVIEW_SELECTION"] if source is self._browser else p["EDITOR_SELECTION"]
        text = self._collapse(source.textCursor().selectedText())
        target = self._editor if source is self._browser else self._browser
        if target is not None:
            cursors = self._mirror_cursors(source, target, text) if len(text) >= 2 else []
            self._set_hl_layer(target, source, cursors, colour)
        own = self._own_cursors(source) if len(text) >= 2 else []
        self._set_hl_layer(source, source, own, colour)

    def _own_cursors(self, source):
        """The source's own selection, one cursor per block.

        A single cursor crossing table cells is painted by Qt as a whole-cell
        range and can cover the other pane's mirror; slicing per block keeps the
        newest colour strictly on top.
        """
        cursor = source.textCursor()
        return self._range_cursors(source, (cursor.selectionStart(), cursor.selectionEnd()))

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

    def _extra_selection(self, cursor, colour: str) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor(colour))
        selection.format.setForeground(QColor(self._pal()["SEL_TEXT"]))
        return selection

    def _clear_highlights(self) -> None:
        self._hl_layers = {}
        self._browser.setExtraSelections([])
        if self._editor is not None:
            self._editor.setExtraSelections([])

    def _paint_selection_overlay(self, widget, painter) -> None:
        """Draw the newest mirror on top of `widget` (Qt paints native over extra).

        Only the mirror needs this: when the pane's own selection is newest it is
        already the native one, painted above everything. (A full-line extension
        of the preview's own selection was tried here and deliberately rolled
        back: opaque bars painted *after* the native pass cover the text, so
        the selected line could no longer be read through its highlight.)
        """
        if widget is self._browser:
            # code cards draw their own outline here: Qt rich text has no
            # block borders, and a painted 1px line never hides glyphs
            self._paint_code_card_borders(painter)
        other = self._editor if widget is self._browser else self._browser
        layers = self._hl_layers.get(widget, [])
        if not layers or other is None:
            return
        owner, cursors, colour = layers[-1]
        if owner is not other:
            return
        tint = QColor(colour)
        tint.setAlpha(HL_OVERLAY_ALPHA)
        for cursor in cursors:
            for rect in self._cursor_rects(widget, cursor):
                painter.fillRect(rect, tint)

    def _paint_code_card_borders(self, painter) -> None:
        """Outline every visible code/JSON card over the painted text.

        The card fill is solid because its per-line blocks use
        `line-height: 100%` (invariant 3), but Qt cannot put a border on a
        block, so the 1px rounded outline is drawn in the viewport overlay
        pass. Runs are in document order, so off-screen cards are skipped by
        character position and the loop stops below the viewport.
        """
        runs = getattr(self, "_code_runs", None)
        if not runs:
            return
        viewport = self._browser.viewport().rect()
        document = self._browser.document()
        # character positions of the visible band let us skip off-screen cards
        # without a cursorRect/layout query for each of them
        top_pos = self._browser.cursorForPosition(viewport.topLeft()).position()
        bottom_pos = self._browser.cursorForPosition(viewport.bottomLeft()).position()
        painter.save()
        painter.setPen(QPen(QColor(self._pal()["BORDER"]), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for run in runs:
            first = document.findBlockByNumber(run.card_start)
            last = document.findBlockByNumber(run.end)
            if not first.isValid() or not last.isValid():
                continue
            if last.position() < top_pos:
                continue
            if first.position() > bottom_pos:
                break
            rect = self._code_run_rect(run)
            if rect.isNull():
                continue
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 6, 6)
        painter.restore()

    @staticmethod
    def _cursor_rects(widget, cursor):
        """Viewport rectangles a cursor's selection covers (one per line)."""
        document = widget.document()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        rects = []
        block = document.findBlock(start)
        while block.isValid() and block.position() < end:
            layout = block.layout()
            base = block.position()
            if layout is not None:
                for index in range(layout.lineCount()):
                    line = layout.lineAt(index)
                    line_start = base + line.textStart()
                    line_end = line_start + line.textLength()
                    segment_start = max(start, line_start)
                    segment_end = min(end, line_end)
                    if segment_end <= segment_start:
                        continue
                    first = QTextCursor(document)
                    first.setPosition(segment_start)
                    last = QTextCursor(document)
                    last.setPosition(segment_end)
                    left_rect = widget.cursorRect(first)
                    right_rect = widget.cursorRect(last)
                    left = min(left_rect.left(), right_rect.left())
                    right = max(left_rect.left(), right_rect.left())
                    top = min(left_rect.top(), right_rect.top())
                    bottom = max(left_rect.bottom(), right_rect.bottom())
                    if right > left and bottom > top:
                        rects.append(QRect(left, top, right - left, bottom - top))
            block = block.next()
        return rects

    @staticmethod
    def _collapse(text: str) -> str:
        """Whitespace is collapsed on the HTML side, so collapse both sides."""
        return re.sub(r"\s+", " ", text.replace("\u2029", " ")).strip()

    # -- copy strip over a preview selection ------------------------------

    _MD_EDGE_MARKS = "*_`"

    def _ensure_copy_popup(self) -> None:
        """Build the small copy strip on first need.

        The buttons take no focus and the panel shows without activating the
        window, so clicking them never disturbs the selection they act on.
        """
        if self._copy_popup is not None:
            return
        panel = QWidget(self)
        panel.setObjectName("copyPopup")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        row = QHBoxLayout(panel)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(2)
        self._copy_plain_btn = QToolButton()
        self._copy_plain_btn.setText("без md")
        self._copy_plain_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._copy_plain_btn.clicked.connect(self._copy_selection_plain)
        self._copy_md_btn = QToolButton()
        self._copy_md_btn.setText("с md")
        self._copy_md_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._copy_md_btn.clicked.connect(self._copy_selection_md)
        for widget in (self._copy_plain_btn, self._copy_md_btn):
            row.addWidget(widget)
        panel.setVisible(False)
        self._copy_popup = panel

    def _update_copy_popup(self, *_args, from_release: bool = False) -> None:
        """Show or hide the copy strip for the current preview selection.

        While the left button is held the strip would only chase the pointer,
        so it waits for the release (the eventFilter calls this again on
        `MouseButtonRelease` with `from_release`: the global button state is
        not reliable mid-delivery of the release event itself). A selection
        made from the search bar is navigation, not a copy request: while the
        find field holds focus the strip stays away.
        """
        cursor = self._browser.textCursor()
        selected = cursor.hasSelection() and bool(cursor.selectedText().strip())
        if not from_release and QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self._hide_copy_popup()
            return
        find_edit = getattr(self, "_find_edit", None)
        if (
            not selected
            or self._reading_mode
            or (find_edit is not None and find_edit.hasFocus())
        ):
            self._hide_copy_popup()
            return
        self._ensure_copy_popup()
        self._hide_code_copy()  # the selection strip takes precedence
        self._refresh_copy_popup_state()
        if self._place_copy_popup():
            self._copy_popup.setVisible(True)
            self._copy_popup.raise_()

    def _hide_copy_popup(self) -> None:
        """Drop the strip and reset the button labels (the flash overwrote them)."""
        self._copy_offscreen = False
        popup = self._copy_popup
        if popup is not None and popup.isVisible():
            popup.setVisible(False)
        if self._copy_plain_btn is not None:
            self._copy_plain_btn.setText("без md")
        if self._copy_md_btn is not None:
            self._copy_md_btn.setText("с md")

    def _reposition_copy_popup(self, *_args) -> None:
        """Keep the strip glued to its selection while the preview scrolls.

        The strip only resurrects by itself when it was hidden *because the
        selection scrolled out of view* (`_copy_offscreen`); hidden on
        purpose (edit-mode entry, re-render, reading mode) it stays away
        until the next selection.
        """
        popup = self._copy_popup
        if popup is None:
            return
        if popup.isVisible():
            self._place_copy_popup()  # hides itself when the selection left the view
        elif (
            self._copy_offscreen
            and self._browser.textCursor().hasSelection()
            and not QApplication.mouseButtons() & Qt.MouseButton.LeftButton
        ):
            self._update_copy_popup()  # scrolled back into view: bring it out again

    def _place_copy_popup(self) -> bool:
        """Park the strip just under the selection's end, above it when tight.

        The end (not the anchor) is where the eye finishes selecting, like the
        browser copy affordance. When that line has scrolled out of the
        viewport the strip is hidden instead of floating over unrelated text;
        the return value says whether it is on screen.
        """
        popup = self._copy_popup
        if popup is None:
            return False
        document = self._browser.document()
        cursor = self._browser.textCursor()
        end = QTextCursor(document)
        end.setPosition(cursor.selectionEnd())
        end_rect = self._browser.cursorRect(end)
        view = self._browser.viewport().rect()
        if not view.intersects(end_rect):
            self._copy_offscreen = True
            popup.setVisible(False)
            return False
        self._copy_offscreen = False
        anchor = self._browser.viewport().mapTo(self, end_rect.bottomLeft())
        size = popup.sizeHint()
        popup.resize(size)
        # right edge just past the selection's end, one line below it — the
        # browser copy affordance spot; it clamps into the view on either side
        x = max(4, min(anchor.x() + 8 - size.width(), self.width() - size.width() - 4))
        y = anchor.y() + 4
        if y + size.height() > self.height() - 4:
            start = QTextCursor(document)
            start.setPosition(cursor.selectionStart())
            top = self._browser.viewport().mapTo(
                self, self._browser.cursorRect(start).topLeft()
            )
            y = max(
                4, min(top.y() - 4 - size.height(), self.height() - size.height() - 4)
            )
        popup.move(x, y)
        return True

    def _refresh_copy_popup_state(self) -> None:
        """Flag the md button when the selection is gone from the editable text."""
        _md, stale = self._md_selection()
        self._copy_stale = stale
        self._copy_plain_btn.setText("без md")  # undo a previous "Скопировано"
        self._copy_md_btn.setText("с md (удалено)" if stale else "с md")
        self._copy_md_btn.setProperty("stale", stale)
        # dynamic properties need a re-polish before QSS picks them up
        self._copy_md_btn.style().unpolish(self._copy_md_btn)
        self._copy_md_btn.style().polish(self._copy_md_btn)

    def _copy_selection_plain(self, _checked: bool = False) -> None:
        """«без md»: the text exactly as the preview shows it."""
        text = self._plain_selection()
        if not text:
            self._hide_copy_popup()
            return
        QApplication.clipboard().setText(text)
        self._copy_done(self._copy_plain_btn, "Скопировано")

    def _copy_selection_md(self, _checked: bool = False) -> None:
        """«с md»: the markdown that produced the selected render text."""
        md, stale = self._md_selection()
        if not md:
            self._hide_copy_popup()
            return
        QApplication.clipboard().setText(md)
        self._copy_done(
            self._copy_md_btn, "Скопировано (из кеша)" if stale else "Скопировано"
        )

    def _copy_done(self, button, label: str) -> None:
        """Flash the confirmation, then let the strip disappear on its own."""
        cursor = self._browser.textCursor()
        span = (cursor.selectionStart(), cursor.selectionEnd())
        button.setText(label)
        self._copy_popup.adjustSize()
        QTimer.singleShot(self.COPY_FLASH_MS, lambda: self._copy_flash_done(span))

    def _copy_flash_done(self, span) -> None:
        """Hide after the flash — unless a *new* selection has taken the strip."""
        cursor = self._browser.textCursor()
        if (cursor.selectionStart(), cursor.selectionEnd()) == span:
            self._hide_copy_popup()

    # -- copy button over a fenced code block -----------------------------

    def _scan_code_blocks(self) -> None:
        """Collect fenced-code cards from the document just rendered.

        Qt's rich-text importer turns every line of a `<pre>` into its own
        block; each of them carries the code background and the left inset from
        the stylesheet. A card is therefore a contiguous run of such blocks,
        directly preceded by its header (`p.code-lang`: SURFACE background,
        muted text, same inset). The header is part of the card for the hover
        button but is kept out of the copied text.
        """
        self._code_runs = []
        self._code_run_by_block = {}
        document = self._browser.document()
        palette = self._pal()
        code_bg = palette["CODE_BG"].lower()
        surface = palette["SURFACE"].lower()
        muted = palette["TEXT_MUTED"].lower()
        start = None
        last = None
        card_start = None
        pending_caption = None
        lines: list[str] = []
        block = document.begin()
        while block.isValid():
            caption = self._is_code_caption(block, surface, muted)
            if caption:
                if start is not None:
                    self._add_code_run(start, last, lines, card_start)
                start, last, lines = None, None, []
                pending_caption = block.blockNumber()
            elif self._is_code_line(block, code_bg):
                if start is None:
                    start = block.blockNumber()
                    # the caption only belongs to a card when it is directly
                    # above the first code line
                    card_start = (
                        pending_caption
                        if pending_caption == start - 1
                        else start
                    )
                    lines = []
                # body lines are separate blocks; U+2028 only appears in a
                # two-line header, but normalizing it keeps the copy robust
                lines.append(block.text().replace("\u2028", "\n"))
                last = block.blockNumber()
                pending_caption = None
            else:
                if start is not None:
                    self._add_code_run(start, last, lines, card_start)
                start, last, lines = None, None, []
                pending_caption = None
            block = block.next()
        if start is not None:
            self._add_code_run(start, last, lines, card_start)

    def _add_code_run(
        self, start: int, end: int, lines: list[str], card_start: int
    ) -> None:
        # a CRLF fence leaves a trailing "\r" that Qt shows as a space on the
        # last line; the copy should mirror the code, not that artefact
        text = "\n".join(lines).rstrip()
        if not text.strip():
            return
        run = _CodeRun(start, end, text, card_start)
        self._code_runs.append(run)
        for number in range(card_start, end + 1):
            self._code_run_by_block[number] = run

    @staticmethod
    def _is_code_line(block, code_bg: str) -> bool:
        """A block painted with the code card background (one code line)."""
        fmt = block.blockFormat()
        background = fmt.background()
        return (
            background.style() != Qt.BrushStyle.NoBrush
            and background.color().name().lower() == code_bg
            and fmt.textIndent() > 0
        )

    @staticmethod
    def _is_code_caption(block, surface: str, muted: str) -> bool:
        """The header bar above a code card (SURFACE bar, muted label)."""
        fmt = block.blockFormat()
        background = fmt.background()
        foreground = block.charFormat().foreground()
        return (
            background.style() != Qt.BrushStyle.NoBrush
            and background.color().name().lower() == surface
            and foreground.style() != Qt.BrushStyle.NoBrush
            and foreground.color().name().lower() == muted
            and fmt.textIndent() > 0
        )

    def _code_run_at(self, point: QPoint):
        """The code card under viewport point `point`, if any."""
        if not self._code_runs:
            return None
        block = self._browser.cursorForPosition(point).block()
        return self._code_run_by_block.get(block.blockNumber())

    def _code_run_rect(self, run) -> QRect:
        """Viewport rectangle the card covers (the language caption included,
        so the button parks over the card's header rather than over code)."""
        document = self._browser.document()
        top_block = document.findBlockByNumber(run.card_start)
        last = document.findBlockByNumber(run.end)
        if not top_block.isValid() or not last.isValid():
            return QRect()
        top = self._browser.cursorRect(QTextCursor(top_block)).top()
        bottom_cursor = QTextCursor(last)
        bottom_cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        bottom = self._browser.cursorRect(bottom_cursor).bottom()
        return QRect(0, top, self._browser.viewport().width(), max(1, bottom - top))

    def _ensure_code_copy(self) -> None:
        """Build the card copy button on first hover.

        It is a child of the preview viewport (not of the document), so it
        floats over the code like the chat-window affordance and never takes
        focus or space from the text.
        """
        if self._code_copy_btn is not None:
            return
        button = QToolButton(self._browser.viewport())
        button.setObjectName("codeCopy")
        button.setText(self.CODE_COPY_LABEL)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.clicked.connect(self._copy_code_block)
        button.installEventFilter(self)  # keeps it up while the pointer is on it
        button.hide()
        # QSS gives the button an 11 px font; measure with it so the fixed
        # width matches what is actually painted (the flash label swaps text)
        probe = QFont(button.font())
        probe.setPixelSize(11)
        metrics = QFontMetrics(probe)
        label_width = max(
            metrics.horizontalAdvance(self.CODE_COPY_LABEL),
            metrics.horizontalAdvance(self.CODE_COPY_DONE),
        )
        button.setFixedWidth(label_width + 24)  # QSS padding + border
        self._code_copy_btn = button

    def _hover_code_copy(self, point: QPoint) -> None:
        """Show the button over the card under the pointer, hide it otherwise."""
        if self._reading_mode or QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self._hide_code_copy()
            return
        run = self._code_run_at(point)
        if run is None:
            self._hide_code_copy()
            return
        self._ensure_code_copy()
        self._code_copy_hide.stop()
        self._code_copy_run = run
        if self._place_code_copy(run):
            button = self._code_copy_btn
            if not button.isVisible():
                button.show()
            button.raise_()

    def _place_code_copy(self, run) -> bool:
        """Park the button in the card's top-right corner, clamped to the view."""
        button = self._code_copy_btn
        if button is None:
            return False
        viewport = self._browser.viewport()
        rect = self._code_run_rect(run)
        if rect.isNull() or rect.bottom() < 0 or rect.top() > viewport.height():
            return False
        size = button.size()
        x = max(6, viewport.width() - size.width() - 10)
        y = max(4, min(rect.top() + 4, viewport.height() - size.height() - 6))
        button.move(x, y)
        return True

    def _reposition_code_copy(self, *_args) -> None:
        """Keep the button glued to its card while the preview scrolls.

        After a scroll the pointer may sit over another card, so the hover is
        re-evaluated when the viewport is under it; otherwise the button just
        follows the card it was opened on (or hides once that card is gone).
        """
        button = self._code_copy_btn
        if button is None or not button.isVisible():
            return
        viewport = self._browser.viewport()
        if viewport.underMouse():
            self._hover_code_copy(viewport.mapFromGlobal(QCursor.pos()))
            return
        if self._code_copy_run is None or not self._place_code_copy(self._code_copy_run):
            self._hide_code_copy()

    def _on_preview_leave(self) -> None:
        """Schedule a hide when the pointer leaves the preview.

        Entering the button itself (or the control panel) also leaves the
        viewport, so the decision is deferred: the delayed checks keep either
        while the pointer is still on it.
        """
        if self._code_copy_btn is not None and self._code_copy_btn.isVisible():
            self._code_copy_hide.start()
        if self._overlay is not None and self._overlay.isVisible():
            self._overlay_hide.start()

    def _maybe_hide_code_copy(self) -> None:
        button = self._code_copy_btn
        if button is not None and button.isVisible() and button.underMouse():
            return
        self._hide_code_copy()

    def _hide_code_copy(self) -> None:
        self._code_copy_hide.stop()
        self._code_copy_run = None
        self._code_copy_flash.stop()
        button = self._code_copy_btn
        if button is None:
            return
        if button.isVisible():
            button.hide()
        self._reset_code_copy_label()

    def _copy_code_block(self, _checked: bool = False) -> None:
        """Put the whole card's code on the clipboard and flash confirmation."""
        run = self._code_copy_run
        if run is None:
            return
        QApplication.clipboard().setText(run.text)
        button = self._code_copy_btn
        if button is None:
            return
        button.setText(self.CODE_COPY_DONE)
        button.setProperty("copied", True)
        button.style().unpolish(button)
        button.style().polish(button)
        self._code_copy_flash.start()

    def _reset_code_copy_label(self) -> None:
        button = self._code_copy_btn
        if button is None:
            return
        button.setText(self.CODE_COPY_LABEL)
        button.setProperty("copied", False)
        button.style().unpolish(button)
        button.style().polish(button)

    def _plain_selection(self) -> str:
        """Selected preview text as plain lines; images contribute nothing."""
        text = self._browser.textCursor().selectedText()
        # a header is one block with a U+2028 line separator, ordinary text
        # separates paragraphs with U+2029; both must copy as real newlines
        text = text.replace("\u2029", "\n").replace("\u2028", "\n")
        return text.replace("\uFFFC", "").strip("\n")

    def _md_selection(self) -> tuple[str, bool]:
        """Markdown behind the preview selection, and whether it is still editable.

        The range is projected through the position map onto `_map_source` —
        the exact text this render was built from — so the copy matches what
        is on screen even while the editor holds an un-converted draft
        (invariant 12; the map is built from the render, so `_map_dirty`
        does not invalidate it). The flag is True when that text no longer
        exists in the editable source (deleted or replaced without a
        re-render): the copy still works, from the render-time cache, and
        the button shows the "удалено" state.
        """
        cursor = self._browser.textCursor()
        if not cursor.hasSelection():
            return "", False
        source = self._map_source or ""
        source_map = self._ensure_map()
        span = source_map.to_source(cursor.selectionStart(), cursor.selectionEnd()) \
            if source_map is not None else None
        if span is None:
            span = self._find_source_span()
        if span is None:
            # no alignment at all (raw HTML): the visible text is the best md
            return self._plain_selection(), False
        start, end = self._md_span_absorbed(source, span[0], span[1])
        md = source[start:end].strip("\n")
        return md, not self._md_still_editable(md)

    def _find_source_span(self):
        """Word-pattern fallback when the map cannot follow the render."""
        needle = self._collapse(self._browser.textCursor().selectedText())
        pattern = self._match_pattern(needle)
        if pattern is None or not self._map_source:
            return None
        match = pattern.match(self._map_source)
        if match.hasMatch():
            return match.capturedStart(), match.capturedEnd()
        return None

    def _md_still_editable(self, md: str) -> bool:
        """Is the copied markdown still part of the text the user can edit?

        The editor (while editing) or the file on disk is the editable truth;
        the render can lag behind it by an un-converted draft. The comparison
        ignores markup (`_plain_key`), so the block survives only while its
        visible text is still there word for word.
        """
        current = (
            self._editor.toPlainText()
            if self._editing() and self._editor is not None
            else self._text
        )
        current = _normalized(current)
        if not self._map_dirty and current == self._map_source:
            return True  # nothing changed since this render: no search needed
        key = self._plain_key(md)
        target = self._plain_key(current)
        if key:
            return key in target
        return md.strip() in current

    @staticmethod
    def _md_span_absorbed(source: str, start: int, end: int) -> tuple[int, int]:
        """Widen a mapped span so it carries whole markdown constructs.

        The render drops markup, so a projected range lands *inside* the
        markers: a selection starting at the first letter of bold text maps
        after the opening `**`, and one covering a link's label ends before
        its `](target)` tail. Emphasis/code runs touching either edge, a
        surrounding `[label](target)` shell, and the line prefix the span
        begins right after (heading, quote, list marker) are pulled in, so
        «с md» copies markdown as it was written.
        """
        marks = DocumentView._MD_EDGE_MARKS
        while start > 0 and source[start - 1] in marks:
            start -= 1
        while end < len(source) and source[end] in marks:
            end += 1
        if start > 0 and source[start - 1] == "[":
            # a selection inside a link's label takes the whole link
            close = source.find("]", end)
            if close != -1 and source[close + 1 : close + 2] == "(":
                tail = source.find(")", close + 2)
                if tail != -1:
                    start -= 1
                    end = tail + 1
        if end < len(source) and source[end] == "]" and source[end + 1 : end + 2] == "(":
            # ...and a label-only selection takes the "] (target)" tail as well
            tail = source.find(")", end + 2)
            if tail != -1:
                end = tail + 1
        line_start = source.rfind("\n", 0, start) + 1
        prefix = re.match(
            r"(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)", source[line_start:start]
        )
        if prefix is not None and prefix.end() == start - line_start:
            start = line_start
        return start, end

    def _resize_for_edit(self) -> None:
        """Grow the window for the editor without moving the left edge (A).

        `x/y` are never changed: only the right edge moves. Growth is capped so
        the preview does not exceed `MEASURE` (B2) and so the window never leaves
        the screen it is on (`availableGeometry`, taskbar aware). A maximized or
        fullscreen window is left untouched — the editor just fits the current
        width. The pre-edit geometry is remembered for `_leave_edit` (live) and
        for `closeEvent` (persistence, invariant 14).
        """
        window = self.window()
        if window is None:
            return
        # always remember where we were: persistence needs it even if no resize
        window._view_geometry = window.saveGeometry()
        self._pre_edit_rect = QRect(window.geometry())
        self._edit_resized = False
        if window.isMaximized() or window.isFullScreen():
            return
        x, y, w, h = window.geometry().getRect()
        screen = window.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            screen_right = avail.x() + avail.width()  # exclusive right edge
        else:
            screen_right = x + SCREEN_FALLBACK
        toc_w = self._toc_pref() if self._toc_panel.isVisible() else 0
        handles = SPLIT_HANDLE * (2 if toc_w else 1)  # preview + editor (+ toc)
        editor_cap = editor_cap_width(self._editor_ratio)
        # B2: widest edit window whose preview is still exactly MEASURE
        cap = toc_w + handles + MEASURE + editor_cap
        # B3: keep the current preview width, add the editor at the ratio
        preview_now = max(self._preview_width, MIN_COLUMN)
        desired = toc_w + handles + preview_now + editor_cap
        target = min(desired, cap, screen_right - x)
        if w > cap:
            target = w  # already wider than the cap: only growth is capped
        target = max(target, MIN_WINDOW_WIDTH)
        if target != w:
            window.setGeometry(x, y, target, h)  # same x/y -> left edge pinned
            self._edit_resized = True

    def _restore_after_edit(self) -> None:
        """Put the window back exactly as before editing, never moving x/y (A).

        Uses the remembered `QRect` (same x/y/w/h), not `restoreGeometry`, so the
        left edge cannot move. If the window was not resized on entry, or is
        maximized/fullscreen now (it was at entry, or the user maximized during
        edit), the window state is left untouched — a deterministic rule.
        """
        window = self.window()
        rect = self._pre_edit_rect
        resized = self._edit_resized
        self._pre_edit_rect = None
        self._edit_resized = False
        if window is None or not resized:
            return
        if window.isMaximized() or window.isFullScreen():
            return
        if rect is not None and rect.isValid() and window.geometry() != rect:
            window.setGeometry(rect)

    def _leave_edit(self) -> None:
        if not self._confirm_discard():
            return
        self._discard_draft()  # leaving edit mode always retires the draft
        self._ensure_overlay()
        self._sync_timer.stop()
        self._sync_source = None
        self._hide_copy_popup()  # the panes are about to move under it
        self._hide_code_copy()
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
        window = self.window()
        self._restore_after_edit()
        if window is not None:
            window._view_geometry = None  # live cycle done; closeEvent persists
        self._edit_btn.setText("Редактировать")
        self._browser.setFocus()
        self._update_hits(self._find_edit.text())  # search back to the preview
        if self._overlay is not None and self._overlay.isVisible():
            self._place_overlay()  # back over the full-width preview
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
        self._hide_code_copy()  # the panes are about to move under the button
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
            self._toc_panel.setVisible(toc_visible)
            panes = main_panes + (1 if toc_visible else 0)
            avail = max(0, content - SPLIT_HANDLE * (panes - 1))
            toc_w = max(TOC_MIN_WIDTH, self._toc_pref()) if toc_visible else 0
            editor_w = editor_width(avail - toc_w, self._editor_ratio) if edit else 0
            toc, editor, preview = layout_panes(
                avail, edit, toc_visible, self._toc_pref(), editor_w
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
        self._discard_draft()  # the draft is no longer needed
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
        self._discard_draft()  # "Discard": the pending draft goes with it
        return True

    def _mark_map_dirty(self) -> None:
        """The editor changed, so its offsets no longer match the last render."""
        self._map_dirty = True

    def _is_unsaved(self) -> bool:
        return self._editing() and self._editor.toPlainText() != self._baseline

    # -- crash-safe drafts -------------------------------------------------

    def _autosave_draft(self) -> None:
        """Timer slot: copy the unsaved editor text to the draft store."""
        if self._is_unsaved():
            drafts.save_draft(self._path, self._editor.toPlainText())

    def _flush_draft(self) -> None:
        """Crash-safe: persist the pending edit right now (on app exit)."""
        if self._is_unsaved():
            drafts.save_draft(self._path, self._editor.toPlainText())

    def _discard_draft(self) -> None:
        """Forget this file's draft (saved, discarded, or left clean)."""
        self._draft_timer.stop()
        drafts.clear_draft(self._path)

    def _maybe_restore_draft(self) -> bool:
        """Offer to restore a pending draft for the file that was just loaded.

        Returns True when the draft text was put into the editor. Kept apart
        from `load` so tests can call (or stub) it without a modal dialog. A
        draft identical to the disk file is dropped silently (nothing to
        restore); a draft the user refuses is deleted too.
        """
        draft = drafts.load_draft(self._path)
        if draft is None:
            return False
        if _normalized(draft) == _normalized(self._text):
            drafts.clear_draft(self._path)
            return False
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Найден несохранённый черновик для {self._path.name}. Восстановить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            drafts.clear_draft(self._path)
            return False
        self._restore_draft(draft)
        return True

    def _restore_draft(self, draft: str) -> None:
        """Enter edit mode with the draft text, dirty against the disk file."""
        if not self._editing():
            self._enter_edit()
        self._editor.setPlainText(draft)
        # stay dirty against the disk file: `_enter_edit` set the baseline to it
        self._editor.document().setModified(True)
        self._update_state()

    def _update_window_title(self, unsaved: bool) -> None:
        """The version is in the title on purpose: it tells builds apart when
        an old copy is still installed somewhere else.

        The title belongs to the host window, which listens for `state_changed`
        and only reflects the *active* tab's bullet. When this view is not
        hosted (legacy standalone use), fall back to the widget's own title.
        """
        window = self.window()
        if window is not self and getattr(window, "_update_title", None) is not None:
            return  # MainWindow decides from the active tab; see state_changed
        self.setWindowTitle(
            f"{self._path.name}{' •' if unsaved else ''} — {APP_NAME} {APP_VERSION}"
        )

    def _update_state(self) -> None:
        """Title bullet, and which of the two buttons make sense right now."""
        editing = self._editing()
        text = self._editor.toPlainText() if editing else self._text
        unsaved = editing and text != self._baseline
        self._unsaved = unsaved
        # restart the crash-safe autosave while dirty, stop it when clean
        if unsaved:
            self._draft_timer.start()
        else:
            self._draft_timer.stop()
        if self._convert_btn is not None:
            self._convert_btn.setEnabled(editing and text != self._rendered)
        if self._save_btn is not None:
            self._save_btn.setEnabled(unsaved)
        # the host redraws the tab label and (if this is the active tab) the
        # window title; it needs to know the moment the dirty state moved
        self.state_changed.emit()
        self._update_window_title(unsaved)

    def _handle_esc(self) -> None:
        # reading mode is the outermost layer: Esc leaves it first and touches
        # neither the autoscroll ring nor the search box
        window = self.window()
        if window is not self and getattr(window, "_reading_mode", False):
            window._leave_reading_mode()
            return
        if self._autoscroll_timer.isActive():
            self._stop_autoscroll()
        elif (
            self._toc_filter is not None
            and self._toc_filter.hasFocus()
            and self._toc_filter.text()
        ):
            self._toc_filter.clear()
        elif self._find_edit is not None and (
            self._find_edit.hasFocus()
            or (
                getattr(self, "_replace_edit", None) is not None
                and self._replace_edit.hasFocus()
            )
        ):
            self._close_find()
        elif self._editing():
            self._leave_edit()
        elif self._overlay is not None and self._overlay.isVisible():
            self._hide_overlay(force=True)

    # -- overlay (hover chrome) -------------------------------------------

    def _build_find_bar(self) -> None:
        """The find row lives inside the overlay, not a status bar.

        The replace row (Ctrl+H) is built here as well but kept hidden until
        the user asks for it, so the plain Ctrl+F bar is exactly as before.
        """
        bar = QWidget()
        bar.setObjectName("findBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Найти в документе")
        self._find_edit.setFixedWidth(220)
        self._find_edit.returnPressed.connect(lambda: self._find_next())
        self._find_edit.textChanged.connect(lambda text: self._update_hits(text, jump=True))
        self._find_edit.installEventFilter(self)  # focus-out releases the pin

        self._find_status = QLabel("")
        self._find_status.setStyleSheet(f"color: {self._pal()['TEXT_MUTED']};")

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
        outer.addLayout(layout)

        # replace row: hidden until Ctrl+H (edit mode only)
        self._replace_row = QWidget()
        self._replace_row.setObjectName("replaceRow")
        replace = QHBoxLayout(self._replace_row)
        replace.setContentsMargins(0, 0, 0, 0)
        replace.setSpacing(6)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("Заменить на")
        self._replace_edit.setFixedWidth(220)
        self._replace_edit.returnPressed.connect(self._replace_next)
        self._replace_edit.installEventFilter(self)  # focus-out releases the pin

        self._replace_btn = QPushButton("Заменить")
        self._replace_btn.clicked.connect(self._replace_next)

        self._replace_all_btn = QPushButton("Заменить все")
        self._replace_all_btn.clicked.connect(self._replace_all)

        for widget in (self._replace_edit, self._replace_btn, self._replace_all_btn):
            replace.addWidget(widget)
        replace.addStretch(1)
        outer.addWidget(self._replace_row)

        self._replace_mode = False
        self._replace_row.setVisible(False)

        self._find_bar = bar
        # the field stays in the overlay for good: showing/hiding it made the
        # overlay change width long after the search was over
        bar.setVisible(True)

    def _set_replace_mode(self, active: bool) -> None:
        """Show or hide the "Заменить на" row (Ctrl+H)."""
        self._replace_mode = bool(active)
        row = getattr(self, "_replace_row", None)
        if row is not None:
            row.setVisible(self._replace_mode)

    def _open_replace(self) -> None:
        """Ctrl+H: make sure there is an editor, then reveal the replace row."""
        self._ensure_overlay()
        if not self._editing():
            self._enter_edit()
        self._set_replace_mode(True)
        self._show_overlay(pin=True)
        self._place_overlay()
        self._find_edit.setFocus()
        self._find_edit.selectAll()
        self._update_hits(self._find_edit.text())

    def _toggle_replace(self) -> None:
        """Ctrl+H: open the replace bar, or refocus it when already open."""
        if getattr(self, "_replace_mode", False):
            self._replace_edit.setFocus()
            self._replace_edit.selectAll()
            return
        self._open_replace()

    def _replace_next(self) -> None:
        """Replace the current match and select the next one (editor only)."""
        if self._editor is None or not self._editing():
            return
        pattern = self._search_pattern(self._find_edit.text())
        if pattern is None:  # empty query: nothing to do
            return
        document = self._editor.document()
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            # only replace when the selection really is the current match
            match = document.find(pattern, cursor.selectionStart())
            if (
                not match.isNull()
                and match.selectionStart() == cursor.selectionStart()
                and match.selectionEnd() == cursor.selectionEnd()
            ):
                cursor.beginEditBlock()
                cursor.insertText(self._replace_edit.text())
                cursor.endEditBlock()
        self._find_next()
        self._update_hits(self._find_edit.text())

    def _replace_all(self) -> int:
        """Replace every match in the editor; returns how many were replaced."""
        if self._editor is None or not self._editing():
            return 0
        pattern = self._search_pattern(self._find_edit.text())
        if pattern is None:  # empty query is a no-op
            return 0
        document = self._editor.document()
        replacement = self._replace_edit.text()
        # collect first, edit second: replacing while searching would shift the
        # positions we are still walking over
        spans = []
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        while True:
            found = document.find(pattern, cursor)
            if found.isNull():
                break
            spans.append((found.selectionStart(), found.selectionEnd()))
            cursor.setPosition(found.selectionEnd())
        if not spans:
            self._find_status.setText("0")
            return 0
        cursor.beginEditBlock()
        try:
            for start, end in reversed(spans):  # back to front keeps offsets valid
                edit = QTextCursor(document)
                edit.setPosition(start)
                edit.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                edit.insertText(replacement)
        finally:
            cursor.endEditBlock()
        self._find_status.setText(f"Заменено: {len(spans)}")
        return len(spans)

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
        self._zoom_label.clicked.connect(self._toggle_zoom_label)

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
        panel.installEventFilter(self)  # entering the panel is still "inside"
        self._overlay = panel

    def _show_overlay(self, pin: bool = False) -> None:
        """Show the hover chrome; `pin` keeps it up until the action ends."""
        if self._reading_mode:  # reading mode: maximum text, no chrome
            return
        self._ensure_overlay()
        if pin:
            self._overlay_pin = True
        self._overlay_hide.stop()
        self._overlay.setVisible(True)
        self._overlay.raise_()
        self._place_overlay()

    def _hide_overlay(self, force: bool = False) -> None:
        """Hide the chrome unless find/replace holds it open (or `force`)."""
        self._overlay_hide.stop()
        if force:
            self._overlay_pin = False
        if self._overlay_pin and not force:
            return
        if self._overlay is not None:
            self._overlay.setVisible(False)

    def _hover_overlay(self, point: QPoint) -> None:
        """The panel lives at the preview's top edge: reveal it there, and get
        out of the way as soon as the pointer moves down into the text."""
        if self._reading_mode:
            return
        if point.y() <= self.OVERLAY_HOT_ZONE:
            self._show_overlay()
        else:
            self._hide_overlay()

    def _maybe_hide_overlay(self) -> None:
        """Hide after the pointer left, unless it is on the panel itself."""
        if self._overlay_pin:
            return
        overlay = self._overlay
        if overlay is None or not overlay.isVisible():
            return
        if overlay.rect().contains(overlay.mapFromGlobal(QCursor.pos())):
            return
        overlay.setVisible(False)

    def _release_overlay_pin(self) -> None:
        """The find field lost focus: the panel no longer has to stay open."""
        self._overlay_pin = False
        if self._overlay is not None and self._overlay.isVisible():
            self._overlay_hide.start()

    def _place_overlay(self) -> None:
        """Centre the overlay over the preview pane, near its top edge."""
        browser = self._browser
        top_left = browser.mapTo(self, browser.rect().topLeft())
        width = min(self._overlay.sizeHint().width(), browser.width() - 24)
        x = top_left.x() + max(12, (browser.width() - width) // 2)
        self._overlay.resize(width, self._overlay.sizeHint().height())
        self._overlay.move(x, top_left.y() + 12)

    def _pane_for(self, obj):
        """The pane a filtered object belongs to (the widget or its viewport).

        `getattr` guards against events arriving while the panes are still being
        assembled in `__init__` (`_toc` is created after the preview).
        """
        if obj is self._browser or obj is self._browser.viewport():
            return self._browser
        toc = getattr(self, "_toc", None)
        if toc is not None and (obj is toc or obj is toc.viewport()):
            return toc
        editor = getattr(self, "_editor", None)
        if editor is not None and (obj is editor or obj is editor.viewport()):
            return editor
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
        # `getattr` guards the construction window: column handles are children
        # of the browser and their creation sends events before `_splitter`
        # exists; an AttributeError in a filter slot aborts the process
        if obj is getattr(self, "_splitter", None) and event.type() == QEvent.Type.Resize:
            # the splitter resizes a step after the window, so its Resize carries
            # the final width; laying out here avoids a stale-width pass
            self._relayout(event.size().width())
            return False
        toc_filter = getattr(self, "_toc_filter", None)
        if obj is toc_filter and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                toc_filter.clear()
                return True
        if obj is getattr(self, "_code_copy_btn", None):
            # the button is a child of the viewport: leaving it either returns
            # the pointer to the code (a move keeps it up) or leaves the window
            # (the delayed check then hides it)
            if event.type() == QEvent.Type.Leave:
                self._code_copy_hide.start()
            elif event.type() == QEvent.Type.Enter:
                self._code_copy_hide.stop()
        if obj is getattr(self, "_overlay", None):
            # entering the panel crosses the viewport (Leave there schedules a
            # hide), so the panel keeps itself alive while the pointer is on it
            if event.type() == QEvent.Type.Enter:
                self._overlay_hide.stop()
            elif event.type() == QEvent.Type.Leave:
                self._overlay_hide.start()
        if obj is getattr(self, "_find_edit", None) or obj is getattr(
            self, "_replace_edit", None
        ):
            if event.type() == QEvent.Type.FocusOut:
                self._release_overlay_pin()
        pane = self._pane_for(obj)
        if pane is None:
            return super().eventFilter(obj, event)
        etype = event.type()
        if pane in (self._browser, self._editor):
            # A left-button PRESS landing on the QTextBrowser WIDGET (the
            # side-margin strips beside the column, invariant 2) reaches
            # QTextControl in widget coordinates, but the document lives in
            # viewport coordinates — un-translated, the offset moves the
            # anchor and a drag started beside the text selects nothing.
            # Translate the press into viewport coordinates (clamped) and
            # re-send it there; after the accepted press Qt's implicit grab
            # delivers the following moves/releases to the viewport itself,
            # already in viewport coordinates. Do NOT translate move/release:
            # re-sending a release into the viewport makes the release path
            # re-enter this filter and recurse (checked: press-only is safe).
            if (
                pane is self._browser
                and obj is self._browser
                and etype in (
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseButtonDblClick,
                )
                and event.button() == Qt.MouseButton.LeftButton
            ):
                viewport = self._browser.viewport()
                point = event.position().toPoint() - viewport.pos()
                point = QPoint(
                    max(0, min(point.x(), viewport.width() - 1)),
                    max(0, min(point.y(), viewport.height() - 1)),
                )
                translated = QMouseEvent(
                    etype,
                    QPointF(point),
                    event.globalPosition(),
                    Qt.MouseButton.LeftButton,
                    event.buttons(),
                    event.modifiers(),
                )
                QApplication.sendEvent(viewport, translated)
                return True
            if etype == QEvent.Type.Wheel:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self._wheel_zoom(pane, event)
                    return True
                if (
                    self._editing()
                    and event.modifiers() & Qt.KeyboardModifier.AltModifier
                ):
                    # Alt+wheel: pull the other pane along with this pane's
                    # scroll. Let the event through so the pane under the pointer
                    # scrolls natively; the timer then maps and moves the other.
                    self._queue_scroll_sync(pane)
                self._stop_autoscroll()
            elif etype == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.MiddleButton:
                    # a middle click over a link is a browser-style "open in a
                    # new tab"; anywhere else it starts the autoscroll ring
                    if pane is self._browser and self._middle_click_link(event.position().toPoint()):
                        return True
                    self._start_autoscroll(pane, event.position().toPoint())
                    return True
                self._stop_autoscroll()
            elif etype == QEvent.Type.MouseMove and self._autoscroll_pane is pane:
                self._move_autoscroll(event.position().toPoint())
                return True
            elif etype == QEvent.Type.MouseMove and pane is self._browser:
                # hover chrome for fenced code: the button sits over the card
                # under the pointer and is gone as soon as it leaves it; the
                # top edge of the preview summons the control panel
                point = event.position().toPoint()
                if obj is self._browser.viewport():
                    self._hover_overlay(point)
                    self._hover_code_copy(point)
                else:
                    self._hide_code_copy()
                    self._hide_overlay()
            elif etype == QEvent.Type.Leave and obj is self._browser.viewport():
                self._on_preview_leave()
            elif (
                etype == QEvent.Type.MouseButtonRelease
                and pane is self._browser
                and event.button() == Qt.MouseButton.LeftButton
            ):
                # the drag-select is over: the copy strip can come out now
                # (`from_release` skips the button-state check, which is not
                # dependable while the release event is being delivered)
                self._update_copy_popup(from_release=True)
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

    def _toggle_find(self) -> None:
        self._set_replace_mode(False)  # Ctrl+F is the plain search
        self._show_overlay(pin=True)  # typing must not be interrupted by hover
        self._find_edit.setFocus()
        self._find_edit.selectAll()
        self._update_hits(self._find_edit.text())

    def _close_find(self) -> None:
        """Clear the query and let the panel go; the field stays built."""
        self._find_edit.clear()
        self._find_status.setText("")
        self._set_replace_mode(False)
        self._hide_overlay(force=True)
        self._search_target().setFocus()

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
        """Per-document shortcuts, active only while this view holds focus.

        They use `WidgetWithChildrenShortcut` on purpose: two tabs would each
        register the same key, and two `WindowShortcut`s on one key in a single
        window make Qt fire `Ambiguous shortcut overload`. Scoping each set to
        its own view keeps them unambiguous. Window-level keys (Ctrl+W/Q/O,
        Ctrl+Tab, Ctrl+1..9) live on `MainWindow` instead.
        """
        actions = {
            "Esc": self._handle_esc,
            "Ctrl+F": self._toggle_find,
            "Ctrl+H": self._toggle_replace,
            "F3": self._find_next,
            "Shift+F3": lambda: self._find_next(backward=True),
            "F5": self.load,
            "Ctrl+R": self.load,
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
            shortcut = QShortcut(QKeySequence(keys), self, slot)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _tab_title(self) -> str:
        """Tab label: the file name, with a bullet while there are edits."""
        return f"{self._path.name}{' •' if self._is_unsaved() else ''}"

    def _zoom_step(self, step: int) -> None:
        """Zoom the pane the user is working in. All three are independent."""
        if self._toc_panel.isVisible() and self._toc.hasFocus():
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
        anchor = self._preview_top_cursor().position()
        self._hide_code_copy()  # the font change moves every card
        self._zoom = max(-8, min(20, level))
        self._apply_preview_zoom()
        self._update_zoom_label()
        self._scroll_preview_to(anchor)
        self._sync_crumbs()

    def _scroll_preview_to(self, position: int) -> None:
        """Scroll the preview so document `position` sits at the viewport top."""
        document = self._browser.document()
        # an external change may have shortened the document: clamp the remembered
        # position so it can never point past the new end
        position = max(0, min(position, max(0, document.characterCount() - 1)))
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

    # -- editor <-> preview pair scroll (Alt+wheel, edit mode only) --------

    def _queue_scroll_sync(self, source) -> None:
        """Coalesce a wheel burst into one pair scroll.

        Triggered only by the Alt+wheel gesture over a pane, so plain scrolling
        never pulls the other pane along.
        """
        if (
            self._syncing_scroll
            or self._in_relayout
            or not self._editing()
            or self._editor is None
            or not self._panel.isVisible()
        ):
            return
        self._sync_source = source
        self._sync_timer.start()

    def _preview_top_position(self) -> int:
        return self._preview_top_cursor().position()

    def _editor_top_position(self) -> int:
        return self._editor.cursorForPosition(
            self._editor.viewport().rect().topLeft()
        ).position()

    def _sync_scroll_now(self) -> None:
        """Apply the pending pair scroll.

        `_syncing_scroll` is held for the whole target move: setting a
        scrollbar emits `valueChanged`, and without the flag that would queue
        the reverse sync and the two panes would chase each other forever.
        """
        source = self._sync_source
        self._sync_timer.stop()
        if source is None:
            return
        if (
            not self._editing()
            or self._editor is None
            or not self._panel.isVisible()
        ):
            self._sync_source = None
            return
        self._syncing_scroll = True
        try:
            if source is self._browser:
                self._sync_editor_from_preview()
            else:
                self._sync_preview_from_editor()
        finally:
            self._syncing_scroll = False
            self._sync_source = None

    def _sync_editor_from_preview(self) -> None:
        """Scroll the editor to the place the preview's top edge shows.

        The position map is exact and tried first; when it is missing (raw
        HTML) or stale (the editor changed since the render) the enclosing
        heading is used, which is coarse but never wrong about the section.
        """
        position = self._preview_top_position()
        mapped = None
        source_map = self._ensure_map()
        if source_map is not None and not self._map_dirty:
            span = source_map.to_source(position, position + 64)
            if span:
                mapped = span[0]
        if mapped is None:
            mapped = self._editor_position_for_section(self._current_section_index())
        if mapped is None:
            return
        self._scroll_editor_to(mapped)
        self._sync_count += 1

    def _sync_preview_from_editor(self) -> None:
        """Scroll the preview to the place the editor's top edge shows."""
        position = self._editor_top_position()
        mapped = None
        source_map = self._ensure_map()
        if source_map is not None and not self._map_dirty:
            span = source_map.to_render(position, position + 64)
            if span:
                mapped = span[0]
        if mapped is not None:
            self._scroll_preview_to(mapped)
            self._sync_count += 1
            return
        # no usable map: fall back to the heading that owns the top line
        if not self._outline or self._editor is None:
            return
        block = self._editor.document().findBlock(position)
        label = self._editor_section_label(block)
        index = self._preview_index_for_label(label) if label else None
        if index is None:
            return
        self._browser.scrollToAnchor(self._outline[index].anchor)
        self._sync_count += 1

    def _editor_position_for_section(self, index: int):
        """Source position of an outline heading (fallback when no map)."""
        if not self._outline or not 0 <= index < len(self._outline):
            return None
        block = self._find_heading_block(self._outline[index].label)
        return block.position() if block is not None else None

    def _scroll_editor_to(self, position: int) -> None:
        """Scroll the editor so document `position` sits at the viewport top.

        `QPlainTextEdit`'s vertical scrollbar is measured in pixels, and
        `blockBoundingGeometry` is in document coordinates, so the block's top
        is exactly the scroll value that pins it to the top edge.
        """
        if self._editor is None:
            return
        document = self._editor.document()
        position = max(0, min(position, max(0, document.characterCount() - 1)))
        block = document.findBlock(position)
        top = int(round(self._editor.blockBoundingGeometry(block).top()))
        bar = self._editor.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(bar.maximum(), top)))

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

    def _toggle_zoom_label(self) -> None:
        """Preview % label: at 100% jump to 200%, otherwise back to 100%."""
        self._set_zoom(10 if self._zoom == 0 else 0)

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
        # the gutter width is font-dependent, so re-reserve it after a zoom
        if hasattr(self._editor, "_update_gutter_width"):
            self._editor._update_gutter_width()

    def _update_editor_zoom_label(self) -> None:
        if getattr(self, "_editor_zoom_label", None) is None:
            return
        self._editor_zoom_label.setText(f"{round((1.0 + 0.1 * self._editor_zoom) * 100)}%")

    def _toggle_editor_zoom_label(self) -> None:
        """Editor % label: at 100% jump to 200%, otherwise back to 100%."""
        self._set_editor_zoom(10 if self._editor_zoom == 0 else 0)

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
        """The overlay's open button: ask the host for new tabs (Ctrl+O)."""
        window = self.window()
        if window is not self and getattr(window, "_open_files_dialog", None) is not None:
            window._open_files_dialog()
            return
        if not self._confirm_discard():
            return
        start = self._settings.value("last_dir", str(Path.home()))
        name, _ = QFileDialog.getOpenFileName(
            self, APP_NAME, start, "Markdown/JSON (*.md *.markdown *.txt *.json);;All files (*)"
        )
        if not name:
            return
        self._navigate(Path(name))


TAB_MIME = "application/x-mdviewer-tab"

# How far the pointer may leave the tab strip (up/down) before a press-drag
# becomes a tear-off. Small, so pulling a tab out of the strip in any direction
# detaches right away, like a browser; horizontal drags along the band reorder.
TAB_DETACH_MARGIN = 12

# The tab currently being dragged. `QDrag` is synchronous and in-process, so a
# module global is a reliable second channel: Qt may hand the drop handler a
# plain base `QMimeData` wrapper, dropping the Python subclass and its `.view`.
_DRAG_VIEW: DocumentView | None = None

# Live `MainWindow`s, in creation order. The session snapshot concatenates the
# tabs of every registered window; a window registers itself in `__init__` and
# is dropped in `closeEvent` (before its `deleteLater` runs, so a torn-off or
# merged window never leaves a dangling entry behind).
_WINDOWS: list["MainWindow"] = []

# Paths of tabs the user closed, newest last; Ctrl+Shift+T reopens one. Shared
# by every window (browser-like), capped so a long session cannot grow forever.
_CLOSED_TABS: list[Path] = []
_CLOSED_LIMIT = 20


def _remember_closed(path: Path) -> None:
    _CLOSED_TABS.append(Path(path))
    del _CLOSED_TABS[:-_CLOSED_LIMIT]


def _live_windows() -> list["MainWindow"]:
    """Registered windows whose C++ object has not been destroyed yet."""
    live = []
    for window in _WINDOWS:
        try:
            if window._tabs is not None:
                live.append(window)
        except RuntimeError:
            continue
    return live


def _window_at(global_pos: QPoint) -> "MainWindow | None":
    """The visible `MainWindow` whose frame contains `global_pos`, if any."""
    for window in reversed(_live_windows()):
        if window.isVisible() and not window.isMinimized():
            if window.frameGeometry().contains(global_pos):
                return window
    return None


def _resolve_tab_drop(source: "MainWindow", view: "DocumentView", global_pos: QPoint) -> None:
    """Finish a tab drag that no tab bar accepted.

    Dropping on *another* window merges the tab there. Dropping anywhere else —
    including over the source window's own content, which is the browser gesture
    of pulling a tab out of the strip — tears it off into a new window at the
    pointer. (A drop back on the source *strip* is handled by `TabBar.dropEvent`,
    so it never reaches here.)
    """
    target = _window_at(global_pos)
    if target is not None and target is not source:
        if source._take_view(view):
            target._adopt_view(view)
            target.activate()
            if source._tabs.count() == 0:
                source.close()
        return
    source._tear_off(view, global_pos)


class _TabMimeData(QMimeData):
    """Drag payload: the `DocumentView` being moved between tab bars.

    The Python attribute is the primary channel; `_DRAG_VIEW` is the fallback
    (see `_resolve_drag_view`), and `TAB_MIME` is always set as the token that
    marks a drag as ours.
    """

    def __init__(self, view: "DocumentView | None" = None):
        super().__init__()
        self.view = view


def _resolve_drag_view(mime) -> "DocumentView | None":
    """The `DocumentView` a drop carries, tolerating a base `QMimeData` wrapper.

    Prefers the payload's own `.view`; falls back to the global set by
    `TabBar._start_drag`, which survives even when Qt loses the subclass.
    """
    view = getattr(mime, "view", None)
    if view is None:
        view = _DRAG_VIEW
    return view


FILE_MIME = "application/x-mdviewer-file"

# The path being dragged out of the tree panel. Same fallback idea as
# `_DRAG_VIEW`: Qt can hand the drop handler a plain base `QMimeData`,
# dropping the Python subclass and its `.path` (invariant 26).
_DRAG_FILE: Path | None = None


def _resolve_drag_file(mime) -> Path | None:
    """The dragged file path, from the payload or the module global."""
    path = getattr(mime, "path", None)
    if path is None:
        path = _DRAG_FILE
    return path if isinstance(path, Path) else None


class _FileMimeData(QMimeData):
    """Drag payload: a file dragged from the tree panel to a tab bar."""

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = path


class TreeFileList(QListWidget):
    """The file list of the tree panel; drags a viewable file to a tab bar.

    Rows of non-viewable files are click-only (they open in the system), so
    `startDrag` simply does nothing for them (Q17). A drop nothing accepts
    opens nothing (Q19).
    """

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(False)
        # clicking a row must not steal focus from the document (Q15)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(path, Path) or not DocumentView._viewable(path):
            return
        global _DRAG_FILE
        mime = _FileMimeData(path)
        mime.setData(FILE_MIME, QByteArray(b"file"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        rect = self.visualItemRect(item)
        if rect.isValid():
            drag.setPixmap(self.grab(rect))
            drag.setHotSpot(self.viewport().mapFromGlobal(QCursor.pos()) - rect.topLeft())
        _DRAG_FILE = path
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            _DRAG_FILE = None


class TabBar(QTabBar):
    """A browser-style tab strip: drag to reorder, pull down to detach.

    Sliding a tab along the strip is left to Qt (`setMovable`) so reordering
    stays smooth. A `QDrag` starts only once the pointer is pulled *out of the
    strip* (below it, or out of the window), which is the tear-off gesture. The
    drag carries the tab's own pixmap; dropping it on another bar inserts at the
    pointer, dropping it on another window merges, and dropping it anywhere else
    opens a new window at the pointer.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._window = None
        self._press_index = -1
        self._press_pos = QPoint()
        self._dragging = False
        self.setAcceptDrops(True)

    # -- dragging a tab out of the bar -------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_index = self.tabAt(event.position().toPoint())
            self._press_pos = event.position().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            not self._dragging
            and self._press_index >= 0
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            pos = event.position().toPoint()
            window = self.window()
            # Leaving the strip band in *any* direction (above or below it), or
            # out of the window, detaches the tab — the browser gesture. A drag
            # that stays along the band is left to Qt for reordering.
            left_band = pos.y() < -TAB_DETACH_MARGIN or pos.y() > self.height() + TAB_DETACH_MARGIN
            outside = window is not None and not window.frameGeometry().contains(
                event.globalPosition().toPoint()
            )
            if left_band or outside:
                moved = (event.position().toPoint() - self._press_pos).manhattanLength()
                if moved >= QApplication.startDragDistance():
                    if self._start_drag():
                        return
        # still along the strip: hand the move to Qt so it reorders the tab
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_index = -1
        self._dragging = False
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> bool:
        global _DRAG_VIEW
        window = self._window
        tabs = getattr(window, "_tabs", None)
        if window is None or tabs is None:
            return False
        if not 0 <= self._press_index < tabs.count():
            return False
        view = tabs.widget(self._press_index)
        if view is None:
            return False
        mime = _TabMimeData(view)
        mime.setData(TAB_MIME, QByteArray(b"tab"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        # the tab follows the cursor, like a browser tab
        rect = self.tabRect(self._press_index)
        if rect.isValid():
            drag.setPixmap(self.grab(rect))
            drag.setHotSpot(self._press_pos - rect.topLeft())
        self._dragging = True
        _DRAG_VIEW = view  # fallback channel while the drag is in flight
        try:
            result = drag.exec(Qt.DropAction.MoveAction)
        finally:
            # clear before any post-drag action; `_tear_off` uses the local
            # `view`, so the global never outlives the drag
            _DRAG_VIEW = None
            self._dragging = False
        # no tab bar accepted the drop (releasing on the source strip is handled
        # by `dropEvent`). A left button that is still down means the drag was
        # cancelled with Esc, not dropped — then the tab stays put.
        if result == Qt.DropAction.IgnoreAction and not (
            QApplication.mouseButtons() & Qt.MouseButton.LeftButton
        ):
            _resolve_tab_drop(window, view, QCursor.pos())
        return True

    # -- accepting a tab dropped onto this bar -----------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(FILE_MIME):
            # a file dragged from the tree panel opens a tab where the pointer
            # is (Q17); the appended tab is moved into place right after
            path = _resolve_drag_file(event.mimeData())
            window = self._window
            if path is None or window is None or not DocumentView._viewable(path):
                event.ignore()
                return
            to = self.tabAt(event.position().toPoint())
            window._add_tab(path)
            count = window._tabs.count()
            if 0 <= to < count - 1:
                self.moveTab(count - 1, to)
            event.acceptProposedAction()
            return
        if not event.mimeData().hasFormat(TAB_MIME):
            super().dropEvent(event)
            return
        view = _resolve_drag_view(event.mimeData())
        window = self._window
        if view is None or window is None:
            event.ignore()
            return
        source = view.window()
        if source is window:
            # dropped back on its own strip: place it where the pointer is
            src = window._tabs.indexOf(view)
            to = self.tabAt(event.position().toPoint())
            if src >= 0 and to >= 0 and to != src:
                self.moveTab(src, to)
            event.acceptProposedAction()
            return
        index = self.tabAt(event.position().toPoint())
        if isinstance(source, MainWindow):
            source._take_view(view)
        window._adopt_view(view, index)
        if (
            isinstance(source, MainWindow)
            and source is not window
            and source._tabs.count() == 0
        ):
            source.close()
        event.acceptProposedAction()


class SettingsDialog(QDialog):
    """One dialog for the settings that used to be scattered over the menus.

    Theme, the default zoom of new tabs, session restore and the two editor
    toggles (wrap, line numbers) are read from `QSettings` when the dialog is
    built and written back by `MainWindow._apply_settings` on OK.
    """

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Настройки")
        self.setModal(True)

        self._theme = QComboBox()
        self._theme.addItem("Тёмная", "dark")
        self._theme.addItem("Светлая", "light")
        self._select_data(self._theme, normalize(settings.value("theme", DEFAULT_THEME)))

        self._zoom = QComboBox()
        for level in DEFAULT_ZOOM_LEVELS:
            self._zoom.addItem(f"{zoom_percent(level)}%", level)
        self._select_data(self._zoom, settings.value("default_zoom", 0, type=int))

        self._session = QCheckBox("Восстанавливать прошлую сессию")
        self._session.setChecked(settings.value("restore_session", False, type=bool))
        self._wrap = QCheckBox("Переносить строки в редакторе")
        self._wrap.setChecked(settings.value("editor_wrap", True, type=bool))
        self._numbers = QCheckBox("Номера строк в редакторе")
        self._numbers.setChecked(settings.value("editor_line_numbers", False, type=bool))

        form = QFormLayout()
        form.addRow("Тема:", self._theme)
        form.addRow("Масштаб по умолчанию:", self._zoom)
        form.addRow("", self._session)
        form.addRow("", self._wrap)
        form.addRow("", self._numbers)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        """Select the entry whose userData equals `value`, else the first one."""
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def values(self) -> dict:
        """The chosen settings, ready for `MainWindow._apply_settings`."""
        return {
            "theme": self._theme.currentData(),
            "default_zoom": self._zoom.currentData(),
            "restore_session": self._session.isChecked(),
            "editor_wrap": self._wrap.isChecked(),
            "editor_line_numbers": self._numbers.isChecked(),
        }


class MainWindow(QMainWindow):
    """The application shell: hosts a `QTabWidget`, one `DocumentView` per tab.

    Window-level concerns (title, geometry, DWM dark caption, close handling,
    Ctrl+W/Q/O, Ctrl+Tab and Ctrl+1..9) live here; everything about a document
    lives in `DocumentView`. Unknown attributes are proxied to the *current*
    view so the existing tools (which drive `viewer.Viewer` directly) keep
    working unchanged.

    Shortcuts are split by scope on purpose: per-document keys live in
    `DocumentView` with `WidgetWithChildrenShortcut`, window keys here with the
    default `WindowShortcut`. Two `WindowShortcut`s for the same key in one
    window would fire Qt's `Ambiguous shortcut overload`.
    """

    _OWN_ATTRS = {
        "_view",
        "_tabs",
        "_settings",
        "_view_geometry",
        "_dark_titled",
        "_title_bar_ready",
        "_reading_mode",
        "_reading_view",
        # the file-tree panel lives on the window (invariant 38); every one of
        # these must stay here, or `__setattr__` would forward assignments to
        # the current DocumentView
        "_tree_splitter",
        "_tree_panel",
        "_tree_list",
        "_tree_folder",
        "_tree_folder_label",
        "_tree_watcher",
        "_tree_timer",
        "_tree_state",
        "_tree_reading_saved",
        "_tree_sized",
        "_tree_wanted",
        "_tree_toggle",
    }

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        object.__setattr__(self, "_view", None)
        object.__setattr__(self, "_tabs", None)
        object.__setattr__(self, "_settings", settings or QSettings())
        object.__setattr__(self, "_view_geometry", None)  # QByteArray (invariant 14)
        object.__setattr__(self, "_dark_titled", False)
        object.__setattr__(self, "_title_bar_ready", False)
        object.__setattr__(self, "_reading_mode", False)
        object.__setattr__(self, "_reading_view", None)
        # tree-panel state; built later in `_build_tree_widgets` (invariant 38)
        object.__setattr__(self, "_tree_splitter", None)
        object.__setattr__(self, "_tree_panel", None)
        object.__setattr__(self, "_tree_list", None)
        object.__setattr__(self, "_tree_folder", None)
        object.__setattr__(self, "_tree_folder_label", None)
        object.__setattr__(self, "_tree_watcher", None)
        object.__setattr__(self, "_tree_timer", None)
        object.__setattr__(self, "_tree_state", {})   # folder -> anchor file name
        object.__setattr__(self, "_tree_reading_saved", False)
        object.__setattr__(self, "_tree_sized", False)
        object.__setattr__(self, "_tree_wanted", False)
        object.__setattr__(self, "_tree_toggle", None)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        geometry = self._settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1000, 800)

        tabs = QTabWidget(self)
        tabs.setDocumentMode(True)
        # The left corner of the tab strip hosts the tree-panel toggle: window
        # chrome sitting right where the panel appears, and it hides with the
        # strip in reading mode exactly like the panel does (Q32). The right
        # corner stays button-free: tabs come only from files.
        tree_toggle = QToolButton()
        tree_toggle.setObjectName("treeToggle")
        tree_toggle.setText("📁")
        tree_toggle.setCheckable(True)
        tree_toggle.setToolTip("Панель файлов")  # no hotkeys in tooltips (invariant 18)
        tree_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # clicking never
        # moves focus out of the document
        tree_toggle.clicked.connect(self._toggle_tree)
        tabs.setCornerWidget(tree_toggle, Qt.Corner.TopLeftCorner)
        object.__setattr__(self, "_tree_toggle", tree_toggle)
        bar = TabBar(tabs)
        bar._window = self
        tabs.setTabBar(bar)  # tear-off + cross-window drops live in TabBar
        # `setTabBar` installs a fresh bar and drops `tabsClosable`/`movable`, so
        # both have to be re-applied *after* the custom bar is in place (otherwise
        # the tabs cannot be dragged and show no close button)
        tabs.setTabsClosable(True)
        tabs.setMovable(True)
        tabs.tabCloseRequested.connect(self._close_tab)
        tabs.tabBar().tabMoved.connect(self._on_tab_moved)
        tabs.tabBar().installEventFilter(self)  # middle click closes a tab
        tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tabs.tabBar().customContextMenuRequested.connect(self._show_tab_menu)
        tabs.currentChanged.connect(self._on_current_changed)
        object.__setattr__(self, "_tabs", tabs)
        # The tree panel and the tab pages share the window width (invariant
        # 38). It is a window-level pane on purpose: tabs switch whole
        # DocumentViews, so the panel cannot live inside one of them.
        self._build_tree_widgets(tabs)
        self.setCentralWidget(self._tree_splitter)
        self.setAcceptDrops(True)  # file drops open tabs (Q18/Q22)
        self._build_window_shortcuts()
        _WINDOWS.append(self)  # two windows exist after tear-off / merge

    # -- compatibility: the window proxies the current document ------------

    def __getattr__(self, name):
        view = self.__dict__.get("_view")
        if view is not None:
            return getattr(view, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in MainWindow._OWN_ATTRS:
            object.__setattr__(self, name, value)
            return
        view = self.__dict__.get("_view")
        if view is not None:
            setattr(view, name, value)
        else:
            object.__setattr__(self, name, value)

    @staticmethod
    def _match_pattern(needle: str):
        """Kept on the window class: tools call `viewer.Viewer._match_pattern`."""
        return DocumentView._match_pattern(needle)

    def _current_view(self) -> DocumentView | None:
        tabs = self._tabs
        if tabs is not None:
            widget = tabs.currentWidget()
            if widget is not None:
                return widget
        return self._view

    # -- documents / tabs --------------------------------------------------

    def _find_tab(self, path: Path) -> int:
        """Index of the tab already showing `path` (resolve()d), or -1."""
        target = Path(path).resolve()
        for index in range(self._tabs.count()):
            view = self._tabs.widget(index)
            if view is not None and view._path == target:
                return index
        return -1

    def _add_tab(self, path: Path) -> DocumentView:
        """Open `path` in a tab, or focus the tab that already shows it."""
        index = self._find_tab(path)
        if index >= 0:
            self._tabs.setCurrentIndex(index)
            view = self._tabs.widget(index)
            remember_recent_file(view._path, self._settings)
            return view
        view = DocumentView(Path(path).resolve(), self._settings)
        view.state_changed.connect(lambda v=view: self._on_view_state_changed(v))
        view.link_requested.connect(self._add_tab)  # Ctrl/middle click -> new tab
        default_zoom = self._settings.value("default_zoom", 0, type=int)
        if default_zoom:  # new tabs open at the user's preferred zoom
            view._set_zoom(default_zoom)
        index = self._tabs.addTab(view, view._tab_title())
        self._tabs.setTabToolTip(index, str(view._path))
        self._tabs.setCurrentIndex(index)
        remember_recent_file(view._path, self._settings)
        self._on_current_changed(self._tabs.currentIndex())
        self._save_session()
        return view

    # -- tear-off / merge --------------------------------------------------

    def _take_view(self, view: DocumentView) -> bool:
        """Detach a tab's widget from this window without deleting it.

        `removeTab` alone leaves the page parented to the tab widget's internal
        stack; `setParent(None)` fully detaches it so the receiving window can
        adopt it cleanly.
        """
        index = self._tabs.indexOf(view)
        if index < 0:
            return False
        self._tabs.removeTab(index)
        view.setParent(None)
        return True

    def _adopt_view(self, view: DocumentView, index: int = -1) -> bool:
        """Host an existing `DocumentView` (arriving from another window).

        Signals were hooked to the previous window, so they are disconnected
        and re-connected here; the widget itself is never recreated, therefore
        all of its state (text, dirty, zoom, history, `_hl_layers`,
        `_source_map`) travels along unchanged.

        Returns False when the file is already open in this window (the
        duplicate is deleted instead).
        """
        if view is None:
            return False
        target = Path(view._path).resolve()
        for i in range(self._tabs.count()):
            other = self._tabs.widget(i)
            if (
                other is not None
                and other is not view
                and Path(other._path).resolve() == target
            ):
                self._tabs.setCurrentIndex(i)
                view.deleteLater()
                return False
        # a transferred view still carries the old host's slots; a view that
        # has never been hosted has none of them, hence the guard
        for signal in (view.state_changed, view.link_requested):
            try:
                signal.disconnect()
            except TypeError:
                pass
        view.state_changed.connect(lambda v=view: self._on_view_state_changed(v))
        view.link_requested.connect(self._add_tab)
        view.setParent(self._tabs)
        if 0 <= index <= self._tabs.count():
            index = self._tabs.insertTab(index, view, view._tab_title())
        else:
            index = self._tabs.addTab(view, view._tab_title())
        self._tabs.setTabToolTip(index, str(view._path))
        self._tabs.setCurrentIndex(index)
        self._on_current_changed(self._tabs.currentIndex())
        self._on_tab_moved()
        return True

    def _tear_off(self, view: DocumentView, global_pos: QPoint) -> None:
        """Move a tab into a fresh window placed at the pointer."""
        if view is None or self._tabs.indexOf(view) < 0:
            return
        if not self._take_view(view):
            return
        new = MainWindow(self._settings)
        new._adopt_view(view)
        new.resize(self.size())
        new.move(global_pos.x() - 80, global_pos.y() - 12)
        new.show()
        new.activate()  # browser-like: the fresh window takes focus
        if self._tabs.count() == 0:
            self.close()

    def _open_paths(self, paths) -> None:
        for path in paths:
            path = Path(path)
            if path.is_file():
                self._add_tab(path)

    def open_document(self, path: Path) -> None:
        """Open one markdown file as a tab (or focus its existing tab)."""
        self._add_tab(path)

    def _open_files_dialog(self) -> None:
        """Ctrl+O: pick one or more files, one tab each."""
        start = self._settings.value("last_dir", str(Path.home()))
        names, _ = QFileDialog.getOpenFileNames(
            self, APP_NAME, start, "Markdown/JSON (*.md *.markdown *.txt *.json);;All files (*)"
        )
        if not names:
            return
        self._settings.setValue("last_dir", str(Path(names[0]).parent))
        self._open_paths([Path(name) for name in names])

    def open_external(self, paths) -> None:
        """Open paths handed over by another process (singleton hand-off)."""
        self._open_paths(paths)
        self.activate()

    def activate(self) -> None:
        """Raise and focus the window (single-instance hand-off)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -- file drops (panel drags and Explorer, Q18/Q22) ---------------------

    @staticmethod
    def _has_viewable_url(mime) -> bool:
        return any(
            url.isLocalFile() and DocumentView._viewable(Path(url.toLocalFile()))
            for url in mime.urls()
        )

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(FILE_MIME) or (mime.hasUrls() and self._has_viewable_url(mime)):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(FILE_MIME) or (mime.hasUrls() and self._has_viewable_url(mime)):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        """A drop on the window body (preview included) opens a new tab
        (Q18); external Explorer drops take only local viewable files —
        folders and other files are ignored, so a stray drop never launches
        anything (Q22)."""
        mime = event.mimeData()
        if mime.hasFormat(FILE_MIME):
            path = _resolve_drag_file(mime)
            if path is not None:
                self._add_tab(path)
                event.acceptProposedAction()
                return
        if mime.hasUrls():
            added = False
            for url in mime.urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.is_file() and DocumentView._viewable(path):
                    self._add_tab(path)
                    added = True
            if added:
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    # -- closing / switching ----------------------------------------------

    def _close_tab(self, index: int) -> None:
        """Close a tab, confirming unsaved edits; the last one closes the window."""
        if self._tabs is None or index < 0:
            return
        view = self._tabs.widget(index)
        if view is None:
            return
        if not view._confirm_discard():
            return
        _remember_closed(view._path)  # Ctrl+Shift+T can bring it back
        self._tabs.removeTab(index)
        view.deleteLater()
        self._sync_tree_selection()  # its «•» bullet must go (closing a
        # non-active tab fires no currentChanged)
        self._save_session()
        if self._tabs.count() == 0:
            self.close()

    def _close_current_tab(self) -> None:
        if self._tabs.count() == 0:
            self.close()
            return
        self._close_tab(self._tabs.currentIndex())

    def _reopen_closed_tab(self) -> None:
        """Ctrl+Shift+T: reopen the most recently closed tab (browser style)."""
        while _CLOSED_TABS:
            path = _CLOSED_TABS.pop()
            if path.is_file():
                self._add_tab(path)
                return

    def _select_tab(self, index: int) -> None:
        """Ctrl+1..9: jump to the n-th tab (no-op when it does not exist)."""
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def _cycle_tab(self, step: int) -> None:
        """Ctrl+Tab / Ctrl+Shift+Tab: wrap around to the neighbour."""
        count = self._tabs.count()
        if count < 2:
            return
        self._tabs.setCurrentIndex((self._tabs.currentIndex() + step) % count)

    # -- file-tree panel («Файлы папки», invariant 38) ----------------------

    def _build_tree_widgets(self, tabs) -> None:
        """Split the central area into [tree panel | tab pages].

        The panel starts hidden; only the app's *first* window honours the
        saved `tree_visible` toggle (Q30), tear-off windows always start
        hidden (Q44) — they are created mid-gesture and must not reflow under
        the pointer.
        """
        wanted = (not _WINDOWS) and self._settings.value("tree_visible", False, type=bool)
        object.__setattr__(self, "_tree_wanted", wanted)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(SPLIT_HANDLE)
        object.__setattr__(self, "_tree_splitter", splitter)
        object.__setattr__(self, "_tree_panel", self._build_tree_panel())
        splitter.addWidget(self._tree_panel)
        splitter.addWidget(tabs)
        # a user drag of the panel edge persists the width (Q29); `setSizes`
        # does not emit `splitterMoved`, so this only sees the mouse
        splitter.splitterMoved.connect(self._on_tree_splitter_moved)
        # folder watch: one debounce per burst, rewatch after every event
        # (Q35, invariant 29 — editors save via temp+rename)
        watcher = QFileSystemWatcher(self)
        watcher.directoryChanged.connect(self._on_tree_dir_changed)
        object.__setattr__(self, "_tree_watcher", watcher)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(FILE_WATCH_DEBOUNCE_MS)
        timer.timeout.connect(self._refresh_tree)
        object.__setattr__(self, "_tree_timer", timer)

    def _build_tree_panel(self) -> QWidget:
        """The panel body: caption + close button, folder badge, the list."""
        panel = QWidget()
        panel.setObjectName("treePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 8, 4)
        header_layout.setSpacing(4)
        caption = QLabel("ФАЙЛЫ ПАПКИ")
        caption.setObjectName("treeCaption")
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # no tooltip (invariant 18)
        close_btn.clicked.connect(self._toggle_tree)
        header_layout.addWidget(caption)
        header_layout.addStretch(1)
        header_layout.addWidget(close_btn)

        # the folder badge: long paths clip instead of widening the panel
        folder_label = QLabel()
        folder_label.setObjectName("treeFolder")
        folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        folder_label.setWordWrap(False)

        tree_list = TreeFileList()
        tree_list.setObjectName("treeList")  # the QSS targets QListWidget#treeList
        tree_list.itemClicked.connect(self._on_tree_clicked)
        tree_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree_list.customContextMenuRequested.connect(self._show_tree_menu)
        tree_list.viewport().installEventFilter(self)  # middle click (Q12)

        layout.addWidget(header)
        layout.addWidget(folder_label)
        layout.addWidget(tree_list, 1)
        object.__setattr__(self, "_tree_list", tree_list)
        object.__setattr__(self, "_tree_folder_label", folder_label)
        panel.setMinimumWidth(TREE_MIN_WIDTH)
        panel.setVisible(False)
        return panel

    def _tree_palette(self) -> dict:
        """Colour tokens for per-item painting (the window owns no `_theme`)."""
        return palette(normalize(self._settings.value("theme", DEFAULT_THEME)))

    def _apply_tree_sizes(self) -> None:
        """Panel at its saved width, the tab pages take the rest.

        A hidden panel must stay at 0: `setSizes` indexes every child,
        hidden ones included (invariant 20).
        """
        if self._tree_splitter is None:
            return
        avail = max(0, self._tree_splitter.width() - SPLIT_HANDLE)
        if self._tree_panel.isVisible():
            width = min(
                self._tree_width_pref(), max(TREE_MIN_WIDTH, avail - 320)
            )
        else:
            width = 0
        self._tree_splitter.setSizes([width, max(0, avail - width)])

    def _tree_width_pref(self) -> int:
        return max(TREE_MIN_WIDTH, int(self._settings.value("tree_width", TREE_WIDTH)))

    def _on_tree_splitter_moved(self, *_args) -> None:
        """A user drag of the panel edge: remember the width (Q29)."""
        if not self._tree_panel.isVisible():
            return
        sizes = self._tree_splitter.sizes()
        if sizes and sizes[0] > 0:
            self._settings.setValue("tree_width", max(TREE_MIN_WIDTH, sizes[0]))

    def _sync_tree_toggle_button(self) -> None:
        """Keep the corner button's check state on the panel's real visibility.

        Every visibility change funnels through here, so the button reads the
        same no matter which of the three toggles (button, hotkey, context
        menu) moved the panel.
        """
        if self._tree_toggle is not None:
            self._tree_toggle.setChecked(
                self._tree_panel is not None and self._tree_panel.isVisible()
            )

    def _toggle_tree(self) -> None:
        """Ctrl+Shift+E / «Панель файлов»: flip the panel's visibility.

        No-op while reading mode hides the chrome (Q32): F11 owns the
        visibility until it exits.
        """
        if self._reading_mode:
            return
        self._set_tree_visible(not self._tree_panel.isVisible())

    def _set_tree_visible(self, visible: bool, persist: bool = True) -> None:
        """Show or hide the panel; persist unless it is an ephemeral change.

        Showing rebuilds the list (the folder may have changed on disk while
        hidden) and re-arms the folder watch; hiding drops the watch.
        """
        if self._tree_panel is None:
            return
        visible = bool(visible)
        if persist:
            self._settings.setValue("tree_visible", visible)
        if self._tree_panel.isVisible() == visible:
            return
        self._tree_panel.setVisible(visible)
        if visible:
            self._sync_tree_folder(force=True)
            self._apply_tree_sizes()
        else:
            self._unwatch_tree()
        self._sync_tree_toggle_button()

    def _sync_tree_folder(self, force: bool = False) -> None:
        """Point the panel at the active tab's folder (Q23: it follows tabs).

        A folder change rebuilds the list and restores the reading anchor
        (Q16); a same-folder switch only refreshes the «•» bullets and the
        highlight. While the panel is hidden only the folder itself is
        tracked, so showing it is one rebuild.
        """
        if self._tree_panel is None:
            return
        view = self._current_view()
        folder = view._path.parent if view is not None else None
        if not force and folder == self._tree_folder:
            if self._tree_panel.isVisible():
                self._sync_tree_selection()
            return
        # anchor the folder we are leaving: its highlighted row (Q16)
        if self._tree_folder is not None:
            item = self._tree_list.currentItem()
            if item is not None:
                path = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(path, Path):
                    self._tree_state[str(self._tree_folder)] = path.name
        self._tree_folder = folder
        if self._tree_panel.isVisible():
            self._populate_tree()
            self._restore_tree_scroll()
            self._update_tree_header()
            self._rewatch_tree()

    def _populate_tree(self) -> None:
        """Rebuild the file list for `_tree_folder`.

        Files only (Q2), natural name sort (Q4), hidden/system files dropped
        (Q5); viewable files normal and the rest dimmed (Q1), files open in
        this window's tabs carry the «•» bullet (Q6). A folder that cannot be
        read shows a notice instead of failing (Q38).
        """
        lst = self._tree_list
        lst.setUpdatesEnabled(False)  # Q37: one paint, however many files
        try:
            lst.clear()
            folder = self._tree_folder
            if folder is None:
                self._append_tree_notice("Нет открытой папки")
                return
            try:
                entries = sorted(
                    (p for p in folder.iterdir() if p.is_file()),
                    key=lambda p: natural_key(p.name),
                )
            except OSError:
                self._append_tree_notice("Папка недоступна")
                return
            entries = [p for p in entries if not is_hidden_file(p)]
            if not entries:
                self._append_tree_notice("Папка пуста")
                return
            muted = QColor(self._tree_palette()["TEXT_MUTED"])
            open_paths = self._open_tab_paths()
            view = self._current_view()
            current_path = view._path if view is not None else None
            for path in entries:
                label = path.name + ("  •" if path in open_paths else "")
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setToolTip(str(path))  # the full path (Q41), no hotkeys
                if not DocumentView._viewable(path):
                    item.setForeground(muted)
                lst.addItem(item)
                if path == current_path:
                    lst.setCurrentItem(item)
        finally:
            lst.setUpdatesEnabled(True)

    def _append_tree_notice(self, text: str) -> None:
        """A non-interactive row («Папка недоступна», «Папка пуста»)."""
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._tree_list.addItem(item)

    def _open_tab_paths(self) -> set:
        """Resolved paths of every tab in this window (for the «•» bullets)."""
        return {
            widget._path
            for index in range(self._tabs.count())
            if (widget := self._tabs.widget(index)) is not None
        }

    def _sync_tree_selection(self) -> None:
        """Refresh the «•» bullets and the active-file highlight in place."""
        lst = self._tree_list
        open_paths = self._open_tab_paths()
        view = self._current_view()
        current_path = view._path if view is not None else None
        target = None
        for row in range(lst.count()):
            item = lst.item(row)
            path = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(path, Path):
                continue
            label = path.name + ("  •" if path in open_paths else "")
            if item.text() != label:
                item.setText(label)
            if current_path is not None and path == current_path:
                target = item
        lst.setCurrentItem(target)  # None clears the highlight

    def _remember_tree_scroll(self) -> None:
        """Anchor the folder being left at its highlighted row (Q16)."""
        if self._tree_folder is None:
            return
        item = self._tree_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self._tree_state[str(self._tree_folder)] = path.name

    def _restore_tree_scroll(self) -> None:
        """Bring the anchor row into view: the active file, else the one
        remembered for this folder — never a raw row index (Q16)."""
        lst = self._tree_list
        target = None
        view = self._current_view()
        if view is not None:
            target = self._tree_item_for(view._path)
        if target is None and self._tree_folder is not None:
            anchor = self._tree_state.get(str(self._tree_folder))
            if anchor:
                target = self._tree_item_for(self._tree_folder / anchor)
        if target is not None:
            lst.scrollToItem(target, QListWidget.ScrollHint.PositionAtTop)
            lst.setCurrentItem(target)

    def _tree_item_for(self, path: Path):
        for row in range(self._tree_list.count()):
            item = self._tree_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                return item
        return None

    def _update_tree_header(self) -> None:
        """The folder badge; a long path clips (Ignored policy), the tooltip
        carries the whole folder."""
        folder = str(self._tree_folder) if self._tree_folder is not None else ""
        self._tree_folder_label.setText(folder)
        self._tree_folder_label.setToolTip(folder)

    def _rewatch_tree(self) -> None:
        """Watch the shown folder; the watch is re-armed after every event
        because editors replace files atomically (invariant 29)."""
        watched = self._tree_watcher.directories()
        if watched:
            self._tree_watcher.removePaths(watched)
        if self._tree_folder is not None and self._tree_folder.is_dir():
            self._tree_watcher.addPath(str(self._tree_folder))

    def _unwatch_tree(self) -> None:
        watched = self._tree_watcher.directories()
        if watched:
            self._tree_watcher.removePaths(watched)

    def _on_tree_dir_changed(self, _path: str) -> None:
        self._rewatch_tree()
        self._tree_timer.start()

    def _refresh_tree(self) -> None:
        """Timer slot: the folder changed on disk — rebuild quietly (Q36)."""
        if self._tree_panel is None or not self._tree_panel.isVisible():
            return
        self._populate_tree()
        self._restore_tree_scroll()

    def _on_tree_clicked(self, item) -> None:
        """A click on a tree row: viewable opens here (or in a new tab with
        Ctrl, Q11/Q12); non-viewable goes to the system (Q10); notice rows
        are inert."""
        path = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(path, Path):
            return
        new_tab = bool(
            QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
        )
        self._open_tree_path(path, new_tab=new_tab)

    def _open_tree_path(self, path: Path, new_tab: bool = False) -> None:
        """Open a tree row: dedup first (Q14), then navigate the current tab
        with history (Q11) or add a tab (Q12); focus returns to the document
        (Q15). Clicking the active file is a no-op (Q11)."""
        if not DocumentView._viewable(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))  # invariant 5
            return
        if new_tab:
            self._add_tab(path)
        else:
            index = self._find_tab(path)
            if index >= 0:
                self._tabs.setCurrentIndex(index)
            else:
                view = self._current_view()
                if view is not None and path != view._path:
                    view._navigate(path)
        view = self._current_view()
        if view is not None:
            view._browser.setFocus()

    def _build_tree_menu(self, item) -> QMenu:
        """Context menu of a tree row (Q40); kept separate so it is testable."""
        menu = QMenu(self)
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(path, Path):
            open_action = menu.addAction("Открыть")
            open_action.triggered.connect(
                lambda _checked=False, p=path: self._open_tree_path(p)
            )
            open_tab = menu.addAction("Открыть в новой вкладке")
            open_tab.triggered.connect(
                lambda _checked=False, p=path: self._open_tree_path(p, new_tab=True)
            )
            copy_action = menu.addAction("Копировать путь")
            copy_action.triggered.connect(
                lambda _checked=False, p=path: self._copy_path(p)
            )
            folder_action = menu.addAction("Открыть в проводнике")
            folder_action.triggered.connect(
                lambda _checked=False, p=path: self._open_folder(p)
            )
        return menu

    def _show_tree_menu(self, pos) -> None:
        self._build_tree_menu(self._tree_list.itemAt(pos)).exec(
            self._tree_list.mapToGlobal(pos)
        )

    def _refresh_tree_after_theme(self) -> None:
        """Theme switch: per-item colours are baked into the rows, so the
        visible list is rebuilt."""
        if self._tree_panel is not None and self._tree_panel.isVisible():
            self._populate_tree()

    def _on_current_changed(self, index: int) -> None:
        view = self._tabs.widget(index) if self._tabs is not None else None
        object.__setattr__(self, "_view", view)
        self._update_title(view._is_unsaved() if view is not None else False)
        if view is not None:
            QTimer.singleShot(0, view._relayout)
        self._sync_tree_folder()  # the panel follows the active tab (Q23)
        # switching tabs while fullscreen carries reading mode to the new view
        if self._reading_mode and view is not None and view is not self._reading_view:
            if self._reading_view is not None:
                self._reading_view._set_reading_mode(False)
            self._reading_view = view
            view._set_reading_mode(True)

    def _on_view_state_changed(self, view: DocumentView) -> None:
        """A tab's dirty state moved: refresh its label; title if it is active."""
        self._refresh_tab(view)
        if view is self._current_view():
            self._update_title(view._is_unsaved())

    def _refresh_tab(self, view: DocumentView) -> None:
        index = self._tabs.indexOf(view)
        if index < 0:
            return
        self._tabs.setTabText(index, view._tab_title())
        self._tabs.setTabToolTip(index, str(view._path))

    def _on_tab_moved(self, *_args) -> None:
        """Remember the user's tab order (restoring it is a later stage)."""
        order = [str(self._tabs.widget(i)._path) for i in range(self._tabs.count())]
        self._settings.setValue("tab_order", order)
        self._save_session()

    # -- session save / restore -------------------------------------------

    def _save_session(self, settings: QSettings | None = None) -> None:
        """Snapshot every open window's tabs when session restore is enabled.

        The snapshot is the concatenation of the registered windows' tabs in
        registry (creation) order; only files that still exist are kept. The
        recorded active tab is the current tab of the window that was last
        active (`QApplication.activeWindow()`), falling back to the last
        registered window when no window reports itself active — a simple,
        deterministic rule that works headless too.
        """
        settings = settings or self._settings
        if not settings.value("restore_session", False, type=bool):
            return
        windows = _live_windows()
        active_window = QApplication.activeWindow()
        chosen = active_window if active_window in windows else (windows[-1] if windows else None)
        paths: list[str] = []
        active = ""
        for window in windows:
            for index in range(window._tabs.count()):
                view = window._tabs.widget(index)
                if view is None:
                    continue
                path = Path(view._path)
                if not path.is_file():
                    continue
                paths.append(str(path))
                if window is chosen and index == window._tabs.currentIndex():
                    active = str(path)
        settings.setValue("session_paths", paths)
        settings.setValue("session_active", active)

    def _restore_session(self) -> bool:
        """Reopen the tabs of the saved session; returns True if any opened.

        `session_active` (if it is among the reopened files) becomes the current
        tab. Missing files are skipped, so a stale snapshot degrades gracefully.
        """
        settings = self._settings
        raw = settings.value("session_paths", [], type=list) or []
        active = settings.value("session_active", "", type=str) or ""
        paths = [Path(item) for item in raw if Path(item).is_file()]
        if not paths:
            return False
        self._open_paths(paths)
        if active:
            index = self._find_tab(Path(active))
            if index >= 0:
                self._tabs.setCurrentIndex(index)
        self._save_session()
        return self._tabs.count() > 0

    # -- "restore session" toggle (tab context menu) -----------------------

    def _tab_view_at(self, pos: QPoint) -> DocumentView | None:
        """The view of the tab under `pos` (the active one when none is hit)."""
        bar = self._tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            index = self._tabs.currentIndex()
        if index < 0:
            return None
        return self._tabs.widget(index)

    def _copy_path(self, path: Path) -> None:
        """Put a local file path on the clipboard."""
        QApplication.clipboard().setText(str(path))

    def _open_folder(self, path: Path) -> None:
        """Open the containing folder in the system file manager (local only)."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _open_recent(self, path: str) -> None:
        """Open a file picked from the "Недавние" submenu, if it still exists."""
        candidate = Path(path)
        if candidate.is_file():
            self._add_tab(candidate)

    def _clear_recent(self) -> None:
        self._settings.setValue(RECENT_KEY, [])

    def _build_tab_menu(self, view: DocumentView | None) -> QMenu:
        """Build the tab context menu for `view` (kept separate so it is testable).

        `_show_tab_menu` execs the result at the pointer. The file and export
        actions target `view`; paths are always local (`DocumentView` only ever
        holds local files), so nothing here reaches the network.
        """
        menu = QMenu(self)
        if view is not None:
            path = view._path
            folder = menu.addAction("Открыть содержащую папку")
            folder.triggered.connect(lambda _checked=False, p=path: self._open_folder(p))
            copy = menu.addAction("Копировать путь")
            copy.triggered.connect(lambda _checked=False, p=path: self._copy_path(p))
            export_menu = menu.addMenu("Экспорт")
            pdf = export_menu.addAction("PDF…")
            pdf.triggered.connect(
                lambda _checked=False, v=view: v.export_via_dialog("pdf")
            )
            html = export_menu.addAction("HTML…")
            html.triggered.connect(
                lambda _checked=False, v=view: v.export_via_dialog("html")
            )
            menu.addSeparator()

        recent_menu = menu.addMenu("Недавние")
        recent = recent_files(self._settings)
        if recent:
            for item in recent:
                action = recent_menu.addAction(Path(item).name)
                action.setToolTip(item)
                action.triggered.connect(lambda _checked=False, p=item: self._open_recent(p))
            recent_menu.addSeparator()
            clear = recent_menu.addAction("Очистить список")
            clear.triggered.connect(self._clear_recent)
        else:
            empty = recent_menu.addAction("Пусто")
            empty.setEnabled(False)

        menu.addSeparator()
        tree_action = menu.addAction("Панель файлов")
        tree_action.setCheckable(True)
        tree_action.setChecked(
            self._tree_panel is not None and self._tree_panel.isVisible()
        )
        tree_action.triggered.connect(lambda _checked=False: self._toggle_tree())
        menu.addSeparator()
        settings_action = menu.addAction("Настройки…")
        settings_action.triggered.connect(
            lambda _checked=False: self._open_settings_dialog()
        )
        return menu

    def _show_tab_menu(self, pos: QPoint) -> None:
        """Right-click on the tab strip: file/export actions, recents, settings."""
        menu = self._build_tab_menu(self._tab_view_at(pos))
        menu.exec(self._tabs.tabBar().mapToGlobal(pos))

    # -- settings dialog (single source for the scattered toggles) ---------

    def _build_settings_dialog(self) -> SettingsDialog:
        """The dialog pre-filled from this window's QSettings (testable)."""
        return SettingsDialog(self._settings, self)

    def _open_settings_dialog(self) -> None:
        """`Ctrl+,` / «Настройки…»: edit the settings, apply them on OK."""
        dialog = self._build_settings_dialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings(dialog.values())
        dialog.deleteLater()

    def _apply_settings(self, values: dict) -> None:
        """Apply a settings-dialog payload to every open window and tab.

        The theme re-renders every document; wrap / line numbers reach existing
        editors; the default zoom is only remembered, so new tabs open at it
        while the ones already open are left alone.
        """
        theme = normalize(values.get("theme", self._theme_name()))
        if theme != self._theme_name():
            self._set_theme(theme)
        self._set_restore_session(bool(values.get("restore_session", False)))
        if "default_zoom" in values:
            self._settings.setValue("default_zoom", int(values["default_zoom"]))
        for window in _live_windows():
            for index in range(window._tabs.count()):
                view = window._tabs.widget(index)
                if view is not None:
                    view._apply_settings(values)

    def _set_restore_session(self, enabled: bool) -> None:
        """Persist the flag; turning it on snapshots the current session at once."""
        self._settings.setValue("restore_session", bool(enabled))
        if enabled:
            self._save_session()

    def _set_default_zoom(self, level: int) -> None:
        """Remember the zoom new tabs open at and apply it to the current tab."""
        self._settings.setValue("default_zoom", int(level))
        view = self._current_view()
        if view is not None:
            view._set_zoom(int(level))

    # -- theme (dark / light, applied app-wide) ----------------------------

    def _theme_name(self) -> str:
        return normalize(self._settings.value("theme", DEFAULT_THEME))

    def _apply_title_bar(self) -> None:
        """Match the DWM caption to the theme (dark caption for the dark theme)."""
        dark = not is_light(self._theme_name())
        set_dark_title_bar(self, dark)
        object.__setattr__(self, "_dark_titled", dark)

    def _set_light_theme(self, enabled: bool) -> None:
        """Tab-context-menu toggle: light theme on, dark theme off."""
        self._set_theme("light" if enabled else "dark")

    def _set_theme(self, name: str) -> None:
        """Persist and apply a theme to every open window and tab.

        The application stylesheet lives on `QApplication`, so one call covers
        the chrome of all windows; each document then re-renders with its own
        stylesheet and table colours, keeping the reading position.
        """
        name = normalize(name)
        self._settings.setValue("theme", name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(app_qss(name))
        for window in _live_windows():
            for index in range(window._tabs.count()):
                view = window._tabs.widget(index)
                if view is not None:
                    view._apply_theme(name)
            window._apply_title_bar()
            window._refresh_tree_after_theme()  # per-item colours (invariant 38)

    # -- window chrome -----------------------------------------------------

    def _title(self, unsaved: bool) -> str:
        """The version is part of the title on purpose: it tells builds apart."""
        view = self._tabs.currentWidget() if self._tabs is not None else None
        name = view._path.name if view is not None else ""
        return f"{name}{' •' if unsaved else ''} — {APP_NAME} {APP_VERSION}"

    def _update_title(self, unsaved: bool) -> None:
        self.setWindowTitle(self._title(unsaved))

    def _build_window_shortcuts(self) -> None:
        """Window-level keys, created once for the whole window."""
        actions = {
            "Ctrl+W": self._close_current_tab,
            "Ctrl+Q": self.close,
            "Ctrl+O": self._open_files_dialog,
            "Ctrl+Shift+T": self._reopen_closed_tab,
            "Ctrl+Tab": lambda: self._cycle_tab(1),
            "Ctrl+Shift+Tab": lambda: self._cycle_tab(-1),
            "Ctrl+,": self._open_settings_dialog,
            "Ctrl+Shift+E": self._toggle_tree,
            "F11": self._toggle_reading_mode,
        }
        for number in range(1, 10):
            actions[f"Ctrl+{number}"] = lambda index=number - 1: self._select_tab(index)
        for keys, slot in actions.items():
            QShortcut(QKeySequence(keys), self, slot)

    # -- reading mode / fullscreen (F11) -----------------------------------

    def _toggle_reading_mode(self) -> None:
        if self._reading_mode:
            self._leave_reading_mode()
        else:
            self._enter_reading_mode()

    def _enter_reading_mode(self) -> None:
        """Fullscreen with maximum text: no tab strip, no outline, no overlay."""
        if self._reading_mode:
            return
        self._reading_mode = True
        self._tabs.tabBar().setVisible(False)
        # the tree panel is window chrome too (Q32); F11 owns its visibility
        # until reading mode exits
        if self._tree_panel is not None and self._tree_panel.isVisible():
            self._tree_reading_saved = True
            self._tree_panel.setVisible(False)
            self._unwatch_tree()
            self._sync_tree_toggle_button()
        view = self._current_view()
        self._reading_view = view
        if view is not None:
            view._set_reading_mode(True)
        self.showFullScreen()

    def _leave_reading_mode(self) -> None:
        """Back to a normal window: restore the tab strip and the outline."""
        if not self._reading_mode:
            return
        self._reading_mode = False
        self._tabs.tabBar().setVisible(True)
        if self._tree_reading_saved:
            self._tree_reading_saved = False
            self._tree_panel.setVisible(True)
            self._sync_tree_folder(force=True)
            self._apply_tree_sizes()
            self._sync_tree_toggle_button()
        view = self._reading_view if self._reading_view is not None else self._current_view()
        self._reading_view = None
        if view is not None:
            view._set_reading_mode(False)
        self.showNormal()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._title_bar_ready:
            object.__setattr__(self, "_title_bar_ready", True)
            self._apply_title_bar()
        view = self._current_view()
        if view is not None:
            QTimer.singleShot(0, view._relayout)
        # first show: honour the saved toggle, then pin the panel width once
        # the splitter knows its real size
        if not self._tree_sized:
            object.__setattr__(self, "_tree_sized", True)
            if self._tree_wanted:
                self._set_tree_visible(True, persist=False)
            QTimer.singleShot(0, self._apply_tree_sizes)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        view = self._current_view()
        if view is None:
            return
        view._set_margins()
        overlay = view._overlay
        if overlay is not None and overlay.isVisible():
            view._place_overlay()

    def closeEvent(self, event):
        # every tab must pass its unsaved check, not just the active one
        for index in range(self._tabs.count()):
            view = self._tabs.widget(index)
            if view is not None:
                # crash-safe: put the pending edit on disk before prompting, so
                # edits survive even if the dialog (or the process) never returns
                view._flush_draft()
            if view is not None and not view._confirm_discard():
                event.ignore()
                return
        view = self._current_view()
        # Closing in edit mode must not remember the widened split window,
        # otherwise the next start looks wide but has no editor.
        if view is not None and view._editing() and self._view_geometry is not None:
            self._settings.setValue("geometry", self._view_geometry)
        else:
            self._settings.setValue("geometry", self.saveGeometry())
        if view is not None:
            self._settings.setValue("last_dir", str(view._path.parent))
        # Session bookkeeping. When other windows stay open, this one leaves the
        # session (it is being closed on purpose). When it is the last window,
        # the app is quitting, so its tabs are kept in the snapshot for the next
        # launch before the entry is dropped.
        if self in _WINDOWS:
            remaining = [window for window in _live_windows() if window is not self]
            if remaining:
                _WINDOWS.remove(self)
                self._save_session()
            else:
                self._save_session()
                _WINDOWS.remove(self)
        super().closeEvent(event)

    def enterEvent(self, event):
        # The hover panel is summoned by the preview's top edge, not by any
        # entry into the window: it used to pop up every time the pointer
        # crossed the frame and sat over the text (invariant 16).
        super().enterEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        # hide only when the pointer really left the window, not just the
        # central widget (the overlay itself counts as inside)
        view = self._current_view()
        if view is not None and not self.underMouse():
            view._hide_overlay()

    def eventFilter(self, obj, event):
        """Tab-bar middle click, then route filter events to the current view."""
        if (
            self._tabs is not None
            and obj is self._tabs.tabBar()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.MiddleButton
        ):
            index = self._tabs.tabBar().tabAt(event.position().toPoint())
            if index >= 0:
                self._close_tab(index)
                return True
        if (
            self._tree_list is not None
            and obj is self._tree_list.viewport()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.MiddleButton
        ):
            item = self._tree_list.itemAt(event.position().toPoint())
            path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(path, Path) and DocumentView._viewable(path):
                self._add_tab(path)  # Q12: middle click opens a new tab
                return True
        view = self._current_view()
        if view is not None:
            return view.eventFilter(obj, event)
        return super().eventFilter(obj, event)



class Viewer(MainWindow):
    """Backwards-compatible single-window view (used by tools/)."""

    def __init__(self, path: Path, settings: QSettings | None = None):
        super().__init__(settings)
        self.open_document(path)