"""Drives the edit mode without a user: open -> edit -> convert -> save.

Run from the project root:
    .venv\\Scripts\\python tools\\edit_check.py
"""

import inspect
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt, QUrl
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPalette, QTextCursor, QWheelEvent
from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QTextBrowser

import theme as theme_mod
import viewer as viewer_mod
from main import create_app
from theme import (
    EDITOR_HARD,
    EDITOR_SELECTION,
    MEASURE,
    MIN_COLUMN,
    PREVIEW_HARD,
    PREVIEW_SELECTION,
    SPLIT_HANDLE,
    side_margin,
)
from viewer import layout_panes, toc_visible_for
from version import APP_NAME, APP_VERSION

SOURCE = "# Title\r\n\r\nHello.\r\n"
UPDATED = "# Title\r\n\r\nHello, world.\r\n"

failures = []


def check(label, got, want):
    ok = got == want
    failures.append(label) if not ok else None
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (want {want!r})"))


def _install_test_screen_placement() -> None:
    """Optionally keep the automated test windows off the primary monitor.

    Set `MDVIEWER_TEST_SCREEN=secondary` (or a screen name) to place every
    Viewer on that screen; otherwise nothing changes.
    """
    want = os.environ.get("MDVIEWER_TEST_SCREEN", "").strip().lower()
    if not want:
        return
    screens = QApplication.screens()
    if want == "secondary":
        target = next((s for s in screens if s is not QApplication.primaryScreen()), None)
    else:
        target = next((s for s in screens if s.name().lower() == want), None)
    if target is None:
        return
    geo = target.availableGeometry()
    original_show = viewer_mod.Viewer.show

    def show_on_target(self):
        size = self.size()
        self.setGeometry(geo.x() + 20, geo.y() + 20, size.width(), size.height())
        original_show(self)

    viewer_mod.Viewer.show = show_on_target


def main() -> int:
    app = create_app()
    _install_test_screen_placement()

    # --- column policy, independent of any window
    check("narrow window keeps minimal insets", side_margin(1000), 20)
    check("column stops at MEASURE", side_margin(1700), 200)
    check("wide window grows the bars", side_margin(3440), 1072)
    check("column never exceeds MEASURE", 3440 - 2 * side_margin(3440) <= MEASURE, True)
    check("text column stays readable", 3440 - 2 * side_margin(3440) >= MIN_COLUMN, True)

    # --- pane shrink policy, pure function traces (avail excludes handles)
    check("read: TOC yields to keep the column", layout_panes(586, False, True, 240, 520), (166, 0, 420))
    check("read: TOC hidden, column takes all", layout_panes(500, False, False, 240, 520), (0, 0, 500))
    check("edit: TOC yields first", layout_panes(954, True, True, 240, 520), (120, 414, 420))
    check("edit: editor yields before the preview", layout_panes(700, True, False, 240, 520), (0, 280, 420))
    check("edit: editor reaches its soft floor", layout_panes(600, True, False, 240, 520), (0, 220, 380))
    check("edit: editor at its hard floor", layout_panes(500, True, False, 240, 520), (0, 220, 280))
    check("edit: surplus goes to the preview", layout_panes(1400, True, True, 240, 520), (240, 520, 640))
    # hidden panes are zero and must not creep back into the layout
    check("hidden TOC stays zero", layout_panes(1000, True, False, 240, 520)[0], 0)
    check("no editor in read mode", layout_panes(1000, False, True, 240, 520)[1], 0)

    # --- auto-hide hysteresis (main_avail excludes only the main handles)
    check("read: outline hidden when tight", toc_visible_for(500, False, True, False), False)
    check("read: outline shown when it fits", toc_visible_for(586, False, True, False), True)
    check("hysteresis: stays hidden below the show edge", toc_visible_for(560, True, True, False), False)
    check("hysteresis: returns above the show edge", toc_visible_for(590, True, True, False), True)
    check("user-hidden outline never auto-returns", toc_visible_for(2000, True, False, False), False)
    check("edit: outline hide threshold", toc_visible_for(900, False, True, True), False)
    check("edit: outline shown when comfortable", toc_visible_for(954, False, True, True), True)

    # --- theme: both palettes build, expose the same tokens and differ
    dark_tokens = set(theme_mod.palette("dark"))
    light_tokens = set(theme_mod.palette("light"))
    check("both palettes expose the same tokens", dark_tokens == light_tokens, True)
    check("the palette is not trivially small", len(dark_tokens) >= 20, True)

    def _referenced(func):
        """Placeholder names the stylesheet f-string interpolates."""
        return {m.group(1) for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", inspect.getsource(func))}

    css_tokens = _referenced(theme_mod.content_css)
    qss_tokens = _referenced(theme_mod.app_qss)
    check("content_css only references known tokens",
          css_tokens <= dark_tokens and css_tokens <= light_tokens, True)
    check("app_qss only references known tokens",
          qss_tokens <= dark_tokens and qss_tokens <= light_tokens, True)
    check("light content css builds", bool(theme_mod.content_css("light")), True)
    check("light app qss builds", bool(theme_mod.app_qss("light")), True)
    check("light content css differs from dark",
          theme_mod.content_css("light") != theme_mod.content_css("dark"), True)
    check("light app qss differs from dark",
          theme_mod.app_qss("light") != theme_mod.app_qss("dark"), True)
    check("bare content_css is the dark one",
          theme_mod.content_css() == theme_mod.content_css("dark"), True)
    check("APP_QSS is the dark one", theme_mod.APP_QSS == theme_mod.app_qss("dark"), True)
    check("an unknown theme falls back to dark",
          theme_mod.normalize("solarized"), theme_mod.DEFAULT_THEME)
    # a pure table retheme: the emitted dark attribute moves to the light palette
    dark_row = f'<tr bgcolor="{theme_mod.palette("dark")["TABLE_ROW_A"]}"><td>x</td></tr>'
    check("table rows are recoloured for the light theme",
          theme_mod.palette("light")["TABLE_ROW_A"]
          in theme_mod.retheme_tables(dark_row, "light"), True)
    check("table retheme is a no-op for dark",
          theme_mod.retheme_tables(dark_row, "dark"), dark_row)

    # --- theme switch at runtime: QSS, QSettings, re-render and DWM title
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "theme.md"
        path.write_text("# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
        settings = QSettings(tempfile.mkdtemp(), "theme_check")
        win = viewer_mod.Viewer(path, settings)
        win.resize(800, 600)
        win.show()
        for _ in range(3):
            app.processEvents()
        check("a new view starts on the dark theme", win._theme, "dark")
        check("a fresh setting defaults to dark",
              settings.value("theme", theme_mod.DEFAULT_THEME, type=str), "dark")
        check("the app starts on the dark QSS",
              app.styleSheet() == theme_mod.app_qss("dark"), True)
        dark_html = win._browser.toHtml()

        win._set_theme("light")
        app.processEvents()
        check("the runtime switch persists the theme",
              settings.value("theme", "", type=str), "light")
        check("the runtime switch sets the light QSS",
              app.styleSheet() == theme_mod.app_qss("light"), True)
        check("the view follows the theme", win._theme, "light")
        check("the window reports the light theme", win._theme_name(), "light")
        check("the light theme drops the dark caption", win._dark_titled, False)
        check("the document uses the light stylesheet",
              win._browser.document().defaultStyleSheet()
              == theme_mod.content_css("light"), True)
        light_html = win._browser.toHtml()
        check("the document really re-rendered", light_html != dark_html, True)
        check("table rows carry the light zebra",
              theme_mod.palette("light")["TABLE_ROW_A"] in light_html, True)
        check("table rows dropped the dark zebra",
              theme_mod.palette("dark")["TABLE_ROW_A"] not in light_html, True)

        win._set_theme("dark")
        app.processEvents()
        check("switching back restores the dark view", win._theme, "dark")
        check("the dark theme restores the dark caption", win._dark_titled, True)
        check("the dark theme is persisted too",
              settings.value("theme", "", type=str), "dark")
        check("the dark QSS is applied again",
              app.styleSheet() == theme_mod.app_qss("dark"), True)
        win.close()
        app.processEvents()

    # --- mutual-highlight search survives markdown markup
    pattern = viewer_mod.Viewer._match_pattern("bold text")
    check("matching skips inline markup", bool(pattern.match("а **bold** text тут").hasMatch()), True)
    pattern = viewer_mod.Viewer._match_pattern("привет мир")
    check("matching works for Cyrillic", bool(pattern.match("сказал **привет** мир!").hasMatch()), True)
    pattern = viewer_mod.Viewer._match_pattern("app/main.py")
    check("matching skips inline code", bool(pattern.match("в `app/main.py` всё").hasMatch()), True)

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.md"
        path.write_bytes(SOURCE.encode("utf-8"))

        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "edit_check"))
        win.resize(1000, 700)
        win.show()
        for _ in range(3):
            app.processEvents()

        # --- read mode
        check("preview has the original text", "Hello." in win._browser.toPlainText(), True)
        check("title carries the version", win.windowTitle(), f"sample.md — {APP_NAME} {APP_VERSION}")
        check("dark title bar requested once", win._dark_titled, True)
        read_preview = win._browser.width()

        # --- enter edit mode
        win._enter_edit()
        for _ in range(3):
            app.processEvents()
        check("preview keeps its width when editing", win._browser.width() >= read_preview - 20, True)
        check("editor is not wider than the preview", win._panel.width() <= win._browser.width() + 20, True)
        check("editor visible", win._editing(), True)
        check("editor holds the source", win._editor.toPlainText().splitlines()[2], "Hello.")
        base_font = win._editor_base_font.pointSizeF()
        preview_zoom = win._zoom
        win._set_editor_zoom(2)
        check("editor zoom scales only the editor", win._editor.font().pointSizeF() > base_font, True)
        check("preview zoom untouched by editor zoom", win._zoom, preview_zoom)
        # Ctrl+wheel over the editor must zoom it, not scroll it
        wheel = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        before_wheel = win._editor_zoom
        win.eventFilter(win._editor, wheel)
        check("Ctrl+wheel zooms the editor", win._editor_zoom, before_wheel + 1)
        # a fine trackpad delta must accumulate, not step on its own
        fine = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 5), QPoint(0, 0),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        win.eventFilter(win._editor, fine)
        check("a fine wheel delta does not zoom yet", win._editor_zoom, before_wheel + 1)
        big = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 40), QPoint(0, 0),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        win.eventFilter(win._editor, big)
        check("accumulated wheel delta zooms once", win._editor_zoom, before_wheel + 2)
        win._set_editor_zoom(0)
        check("editor zoom reset restores the font", win._editor.font().pointSizeF(), base_font)
        # mutual highlighting: each pane shows the newest selection on top, and
        # the other pane's earlier selection stays underneath
        editor_cursor = win._editor.textCursor()
        editor_cursor.setPosition(9)
        editor_cursor.setPosition(14, QTextCursor.MoveMode.KeepAnchor)
        win._editor.setTextCursor(editor_cursor)
        win._highlight_preview_from_editor()
        browser_layers = win._browser.extraSelections()
        check("editor selection mirrors into the preview", len(browser_layers), 1)
        check("preview mirror uses the editor colour", browser_layers[-1].format.background().color().name(), EDITOR_SELECTION)
        check("editor draws its own selection layer", len(win._editor.extraSelections()), 1)

        preview_cursor = win._browser.document().find("Hello")
        win._browser.setTextCursor(preview_cursor)
        win._highlight_editor_from_preview()
        editor_layers = win._editor.extraSelections()
        check("editor keeps both layers", len(editor_layers), 2)
        check("newest layer wins in the editor", editor_layers[-1].format.background().color().name(), PREVIEW_SELECTION)
        check("older editor layer stays underneath", editor_layers[0].format.background().color().name(), EDITOR_SELECTION)
        browser_layers = win._browser.extraSelections()
        check("preview keeps both layers", len(browser_layers), 2)
        check("newest layer wins in the preview", browser_layers[-1].format.background().color().name(), PREVIEW_SELECTION)

        # select in the editor again: its colour returns on top, preview stays
        win._editor.setTextCursor(editor_cursor)
        win._highlight_preview_from_editor()
        editor_layers = win._editor.extraSelections()
        check("editor colour wins after re-selecting", editor_layers[-1].format.background().color().name(), EDITOR_SELECTION)
        check("preview layer remains underneath", editor_layers[0].format.background().color().name(), PREVIEW_SELECTION)
        # Qt paints the native selection over extraSelections, so the newest
        # mirror is drawn by hand on top of the widget
        check("panes draw a custom selection overlay",
              callable(win._browser.overlay_painter), True)
        mirror_cursor = win._browser.extraSelections()[-1].cursor
        check("overlay computes rectangles to paint",
              len(win._cursor_rects(win._browser, mirror_cursor)) > 0, True)
        check("newest layer in the preview is the editor mirror",
              win._hl_layers[win._browser][-1][0] is win._editor, True)
        win._clear_highlights()
        check("clearing drops both highlights",
              len(win._browser.extraSelections()) + len(win._editor.extraSelections()), 0)
        check("window widened", win.width() > 1000, True)
        check("editor panel visible", win._panel.isVisible(), True)
        sizes = win._splitter.sizes()
        check("outline, preview and editor are three panes", len(sizes), 3 if win._toc.isVisible() else 2)
        check("splitter handle is wide enough to grab", win._splitter.handleWidth(), SPLIT_HANDLE)
        check("preview gets the middle", sizes[-2] > 400, True)
        check("editor gets the right part", sizes[-1] > 400, True)
        # setViewportMargins shrinks the viewport itself, so its width *is* the column
        column = win._browser.viewport().width()
        check("preview column keeps a usable width", column > 500, True)
        check("insets follow the preview column, not the window", win._side, side_margin(win._browser.contentsRect().width()))
        check("save disabled while clean", win._save_btn.isEnabled(), False)
        check("convert disabled while clean", win._convert_btn.isEnabled(), False)

        # --- type something
        win._editor.setPlainText(UPDATED)
        check("title marks unsaved changes", win.windowTitle(), f"sample.md • — {APP_NAME} {APP_VERSION}")
        check("save enabled after edit", win._save_btn.isEnabled(), True)
        check("convert enabled after edit", win._convert_btn.isEnabled(), True)

        # --- convert: preview updates, disk untouched
        win._convert()
        check("preview re-rendered", "Hello, world." in win._browser.toPlainText(), True)
        check("convert disabled after convert", win._convert_btn.isEnabled(), False)
        check("still unsaved after convert", win.windowTitle(), f"sample.md • — {APP_NAME} {APP_VERSION}")
        check("disk untouched by convert", path.read_bytes(), SOURCE.encode("utf-8"))

        # --- save: file written, preview already current
        check("save reports success", win._save(), True)
        check("file written as CRLF", path.read_bytes(), UPDATED.encode("utf-8"))
        check("no BOM added to a plain utf-8 file", path.read_bytes()[:3], b"# T")
        check("title clean after save", win.windowTitle(), f"sample.md — {APP_NAME} {APP_VERSION}")
        check("save disabled again", win._save_btn.isEnabled(), False)

        # --- encoding round-trip: cp1251 stays cp1251
        legacy = Path(folder) / "legacy.md"
        legacy.write_bytes("# Заголовок\n".encode("cp1251"))
        win2 = viewer_mod.Viewer(legacy, QSettings(tempfile.mkdtemp(), "edit_check"))
        win2.show()
        win2._enter_edit()
        win2._editor.setPlainText("# Заголовок\n\nдобавлено\n")
        win2._save()
        raw = legacy.read_bytes()
        check("cp1251 preserved on save", raw.decode("cp1251").endswith("добавлено\n"), True)
        try:
            raw.decode("utf-8")
            utf8_ok = False
        except UnicodeDecodeError:
            utf8_ok = True
        check("file is not utf-8 after save", utf8_ok, True)

        # --- leave edit mode
        win._leave_edit()
        check("editor hidden after leave", win._editing(), False)
        check("window restored", win.width() <= 1000, True)

        # --- convert without save, then leave: preview must fall back to disk
        win._enter_edit()
        win._editor.setPlainText("# Draft\n\nчерновик\n")
        win._convert()
        check("draft preview shown", "черновик" in win._browser.toPlainText(), True)
        check("draft not written", path.read_bytes(), UPDATED.encode("utf-8"))
        win._confirm_discard = lambda: True  # stand in for the "Discard" answer
        win._leave_edit()
        check("preview back to the saved file", "черновик" in win._browser.toPlainText(), False)
        check("saved text still on screen", "Hello, world." in win._browser.toPlainText(), True)

        win.close()
        win2.close()
        app.processEvents()

    # --- outline: headings, anchors, scrolling and visibility
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "outline.md"
        body = "# One\n\n" + "\n\n".join(f"## Section {i}\n\n" + "text line\n" * 8 for i in range(40))
        path.write_text(body, encoding="utf-8")
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "toc_check"))
        win.resize(1000, 700)
        win.show()
        for _ in range(3):
            app.processEvents()
        check("outline has every heading", win._toc.count(), 41)
        check("outline visible when there are headings", win._toc.isVisible(), True)
        check("outline wraps long headings instead of eliding", win._toc.wordWrap(), True)
        check("anchors injected", win._browser.toHtml().count("<a name=\"md-h"), 41)
        # the outline has its own zoom, independent of the other panes
        toc_base = win._toc_base_font.pointSizeF()
        win._set_toc_zoom(3)
        check("outline zoom scales its font", win._toc.font().pointSizeF() > toc_base, True)
        check("outline zoom leaves the preview alone", win._zoom, 0)
        win._set_toc_zoom(0)
        check("outline zoom reset restores the font", win._toc.font().pointSizeF(), toc_base)
        # the outline can be dragged wider and the width sticks
        win._splitter.setSizes([360, win._splitter.sizes()[1] - 120])
        win._on_splitter_moved()
        check("outline width is saved", int(win._settings.value("toc_width")), win._splitter.sizes()[0])
        check("outline is draggable past the default", win._splitter.sizes()[0] > 240, True)
        win._sync_outline()
        check("current section resolved from the viewport", win._current_section_index(), 0)
        check("first section is current at the top", win._toc.currentRow(), 0)
        win._overlay.setVisible(False)
        win._show_overlay()
        check("overlay appears on demand", win._overlay.isVisible(), True)
        check("overlay carries the edit button", win._edit_btn.parent() is win._overlay, True)
        # zoom scales both panes and the label reflects it
        check("zoom label starts at 100%", win._zoom_label.text(), "100%")
        win._set_zoom(win._zoom + 1)
        check("zoom label follows the factor", win._zoom_label.text(), "110%")
        win._reset_zoom()
        check("zoom resets to 100%", win._zoom_label.text(), "100%")
        # "100%" has to return to the exact base size; `zoomIn/zoomOut` drifted
        base_doc_font = win._browser.document().defaultFont().pointSizeF()
        base_doc_height = win._browser.document().size().height()
        win._set_zoom(2)
        check("preview font scales with zoom", win._browser.document().defaultFont().pointSizeF() > base_doc_font, True)
        check("preview document grows with zoom", win._browser.document().size().height() > base_doc_height, True)
        win._reset_zoom()
        check("100% restores the exact base font", win._browser.document().defaultFont().pointSizeF(), base_doc_font)
        check("100% restores the exact document height", abs(win._browser.document().size().height() - base_doc_height) < 1.0, True)
        # Qt delivers wheel events to the viewport, so the filter must live
        # there too; otherwise the built-in Ctrl+wheel zoom moves the font while
        # `_zoom` and the "100%" label never change
        preview_wheel = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(win._browser.viewport(), preview_wheel)
        check("preview wheel reaches the handler", win._zoom, 1)
        check("preview zoom label tracks the wheel", win._zoom_label.text(), "110%")
        check("preview font follows the label", win._browser.document().defaultFont().pointSizeF() > base_doc_font, True)
        win._reset_zoom()
        last = win._toc.item(win._toc.count() - 1)
        win._outline_clicked(last)
        for _ in range(6):
            app.processEvents()
        win._sync_outline()
        check("clicking scrolls the preview", win._browser.verticalScrollBar().value() > 0, True)
        # the last section cannot sit at the very top (the file ends), so the
        # highlighted row is near the end rather than exactly the last one
        check("current section follows the click", win._toc.currentRow() > win._toc.count() // 2, True)
        win._toggle_outline()
        check("outline can be hidden", win._toc.isVisible(), False)
        check("visibility persisted", win._settings.value("toc_visible", True, type=bool), False)
        win._toggle_outline()
        check("outline comes back", win._toc.isVisible(), True)

        # --- breadcrumbs: the heading path of the current section
        win._outline_clicked(win._toc.item(5))  # "## Section 4" under "# One"
        for _ in range(8):
            app.processEvents()
        win._sync_crumbs()
        check("breadcrumbs appear inside a section", win._crumbs.isVisible(), True)
        check("breadcrumbs show the ancestor and the current heading",
              "One" in win._crumbs.text() and "Section 4" in win._crumbs.text(), True)
        check("breadcrumbs encode the ancestor as a link",
              'href="0"' in win._crumbs.text(), True)
        win._crumb_clicked("0")
        for _ in range(8):
            app.processEvents()
        win._sync_outline()
        win._sync_crumbs()
        check("crumb click jumps to its heading", win._current_section_index(), 0)
        check("crumb click updates the picked section", win._selected_section, 0)
        check("breadcrumbs hide on a bare top heading", win._crumbs.isVisible(), False)

        # --- outline filter: hides non-matching rows, Esc clears
        win._toc_filter.setText("Section 3")
        for _ in range(2):
            app.processEvents()
        shown = [r for r in range(win._toc.count()) if not win._toc.item(r).isHidden()]
        check("filter hides the non-matching rows", len(shown) < win._toc.count(), True)
        check("filter drops the root heading", win._toc.item(0).isHidden(), True)
        check("filter keeps only matching rows",
              all("Section 3" in win._toc.item(r).text() for r in shown), True)
        check("filter keeps the matching section", win._toc.item(4).isHidden(), False)
        check("outline stays visible while filtering", win._toc_panel.isVisible(), True)
        win._populate_outline()  # a rebuild must re-apply the filter
        for _ in range(2):
            app.processEvents()
        check("filter re-applies after a rebuild", win._toc.item(0).isHidden(), True)
        escape_key = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        win.eventFilter(win._toc_filter, escape_key)
        for _ in range(2):
            app.processEvents()
        check("Esc in the filter clears it", win._toc_filter.text(), "")
        check("clearing restores every row",
              all(not win._toc.item(r).isHidden() for r in range(win._toc.count())), True)

        # --- search: PyQt6 wants a FindFlag, a bool used to abort the process
        win._toggle_find()
        win._find_edit.setText("text line")
        app.processEvents()
        check("search counts hits", int(win._find_status.text()) > 0, True)
        win._find_next()
        check("search selects a match", win._browser.textCursor().hasSelection(), True)
        win._find_next(backward=True)
        check("search goes backwards", win._browser.textCursor().hasSelection(), True)
        # Qt collapses whitespace when rendering, so a query with extra spaces
        # or a leading space must still match
        win._find_edit.setText("  text   line")
        app.processEvents()
        check("search ignores extra spaces", int(win._find_status.text()) > 0, True)
        win._close_find()
        check("search clears the query", win._find_edit.text(), "")
        check("search field stays in the overlay", win._find_bar.isVisible(), True)

        # --- search targets the editor while editing
        win._enter_edit()
        for _ in range(3):
            app.processEvents()
        win._find_edit.setText("Section 7")
        app.processEvents()
        check("editor search finds the source", int(win._find_status.text()) >= 1, True)
        win._find_next()
        check("editor search selects a match", win._editor.textCursor().hasSelection(), True)
        win._confirm_discard = lambda: True
        win._leave_edit()
        for _ in range(3):
            app.processEvents()

        # --- entering edit opens the source at the preview's current zoom
        win._set_zoom(4)
        win._enter_edit()
        for _ in range(3):
            app.processEvents()
        check("editor opens at the preview zoom", win._editor_zoom, 4)
        check("editor font reflects that zoom", win._editor.font().pointSizeF() > win._editor_base_font.pointSizeF(), True)
        win._confirm_discard = lambda: True
        win._leave_edit()
        win._set_zoom(0)
        for _ in range(3):
            app.processEvents()

        # --- entering edit puts the caret on the section on screen, not line 0
        target = win._toc.count() // 2
        win._toc.setCurrentRow(target)
        win._outline_clicked(win._toc.item(target))
        for _ in range(8):
            app.processEvents()
        win._sync_outline()
        # the row the user picked in the outline is honoured, even when the
        # section at the top viewport pixel differs
        wanted = win._outline[target].label
        preview_top = win._browser.cursorForPosition(win._browser.viewport().rect().topLeft()).block()
        win._enter_edit()
        for _ in range(4):
            app.processEvents()
        caret = win._editor.textCursor().block().text()
        check("caret lands on the selected section", win._heading_key(caret), win._heading_key(wanted))
        check("entering edit keeps the preview in place",
              win._browser.cursorForPosition(win._browser.viewport().rect().topLeft()).block().blockNumber(),
              preview_top.blockNumber())
        check("target sits at the top of the editor",
              win._editor.cursorForPosition(win._editor.viewport().rect().topLeft()).block().blockNumber(),
              win._editor.textCursor().block().blockNumber())

        # --- middle-click autoscroll, independently per pane
        def middle_press(pane, x, y):
            event = QMouseEvent(
                QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
                Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton,
                Qt.KeyboardModifier.NoModifier,
            )
            win.eventFilter(pane, event)

        editor_bar = win._editor.verticalScrollBar()
        preview_before = win._browser.verticalScrollBar().value()
        middle_press(win._editor.viewport(), 200, 200)
        check("middle-click arms the editor", win._autoscroll_pane is win._editor, True)
        win._move_autoscroll(QPointF(200, 300).toPoint())
        for _ in range(20):
            win._autoscroll_tick()
        check("editor autoscrolls", editor_bar.value() > 0, True)
        check("preview stays put while the editor scrolls", win._browser.verticalScrollBar().value(), preview_before)
        win._handle_esc()
        check("Esc stops autoscroll", win._autoscroll_pane, None)
        check("indicator hidden after stop", win._scroll_indicator.isVisible(), False)

        # --- selection is colour-coded per pane
        preview_sel = win._browser.palette().color(QPalette.ColorRole.Highlight)
        editor_sel = win._editor.palette().color(QPalette.ColorRole.Highlight)
        check("preview selection is orange", preview_sel.name(), PREVIEW_SELECTION)
        check("editor selection is teal", editor_sel.name(), EDITOR_SELECTION)
        check("the two selections differ", preview_sel != editor_sel, True)

        win._confirm_discard = lambda: True
        win._leave_edit()
        for _ in range(3):
            app.processEvents()

        win.close()
        app.processEvents()

    # --- entering edit lands on the visible paragraph, not just the heading
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "paragraphs.md"
        lines = ["# Doc", ""]
        for i in range(40):
            lines.append(f"## Section {i}")
            lines.append("")
            for j in range(6):
                lines.append(f"Paragraph {i}-{j} with unique trailing words.")
                lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "para_check"))
        win.resize(1000, 600)
        win.show()
        for _ in range(3):
            app.processEvents()

        wanted = "Paragraph 20-3 with unique trailing words."
        block = win._browser.document().find(wanted).block()
        top = win._browser.document().documentLayout().blockBoundingRect(block).top()
        win._browser.verticalScrollBar().setValue(int(top))
        for _ in range(3):
            app.processEvents()
        visible = win._visible_paragraph()
        check("a paragraph sits at the viewport top", visible.startswith("Paragraph"), True)

        win._enter_edit()
        for _ in range(4):
            app.processEvents()
        caret = win._editor.textCursor().block().text()
        check("caret lands on the visible paragraph", win._plain_key(caret), win._plain_key(visible))
        check("caret is not merely the section heading", caret.lstrip().startswith("#"), False)

        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- linked markdown opens in this window, with a working "Назад"
    with tempfile.TemporaryDirectory() as folder:
        first = Path(folder) / "first.md"
        second = Path(folder) / "second.md"
        first.write_text("# Первый\r\n\r\nСмотри [второй](second.md).\r\n", encoding="utf-8")
        second.write_text("# Второй\r\n\r\n## Раздел\r\n\r\nтело второго.\r\n", encoding="utf-8")

        win = viewer_mod.Viewer(first, QSettings(tempfile.mkdtemp(), "nav_check"))
        win.resize(900, 700)
        win.show()
        for _ in range(3):
            app.processEvents()
        check("Qt's own link handling is off", win._browser.openLinks(), False)
        win._show_overlay()
        check("back is disabled at the start", win._back_btn.isEnabled(), False)
        check("the watch follows the open file", win._watch, first.resolve())
        check("the watcher holds the open file", str(first.resolve()) in win._watcher.files(), True)

        # follow the link exactly as a click would
        win._browser.anchorClicked.emit(QUrl.fromLocalFile(str(second)))
        for _ in range(3):
            app.processEvents()
        check("linked file opened in the same window", win._path, second.resolve())
        check("linked file rendered with the theme", "тело второго." in win._browser.toPlainText(), True)
        check("outline rebuilt for the linked file", win._toc.count(), 2)
        check("back is enabled after following a link", win._back_btn.isEnabled(), True)
        check("navigation re-arms the watch on the new file", win._watch, second.resolve())
        check("the old file is no longer watched", str(first.resolve()) in win._watcher.files(), False)

        win._go_back()
        for _ in range(3):
            app.processEvents()
        check("back returns to the previous file", win._path, first.resolve())
        check("previous file is rendered again", "Смотри" in win._browser.toPlainText(), True)
        check("back is disabled once history is empty", win._back_btn.isEnabled(), False)
        check("going back re-arms the watch too", win._watch, first.resolve())

        win.close()
        app.processEvents()

    # --- code cards hit-test correctly (a table cell would jump to its last line)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "code.md"
        body = "# Doc\n\n```\n" + "\n".join(f"line {i:02d} payload" for i in range(40)) + "\n```\n\nAfter.\n"
        path.write_text(body, encoding="utf-8")
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "code_check"))
        win.resize(700, 300)
        win.show()
        for _ in range(3):
            app.processEvents()
        doc = win._browser.document()
        lay = doc.documentLayout()
        first = doc.find("line 00").block()
        check("code block is not wrapped in a table", QTextCursor(first).currentTable(), None)
        win._browser.verticalScrollBar().setValue(int(lay.blockBoundingRect(first).top()))
        for _ in range(2):
            app.processEvents()
        spacing = lay.blockBoundingRect(doc.find("line 01").block()).top() - lay.blockBoundingRect(first).top()
        hit = win._browser.cursorForPosition(QPoint(8, int(spacing * 1.5))).block().text()
        check("click inside a code block maps to that line", hit.startswith("line 01"), True)
        win.close()
        app.processEvents()

    # --- Ctrl+wheel keeps the top of the preview in place
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "long.md"
        body = "# Doc\n\n" + "\n\n".join(f"## Section {i}\n\n" + "text line\n" * 6 for i in range(30))
        path.write_text(body, encoding="utf-8")
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "zoom_check"))
        win.resize(800, 400)
        win.show()
        for _ in range(3):
            app.processEvents()
        bar = win._browser.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        for _ in range(2):
            app.processEvents()
        before = win._browser.cursorForPosition(win._browser.viewport().rect().topLeft()).block().blockNumber()
        wheel = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(win._browser.viewport(), wheel)
        for _ in range(3):
            app.processEvents()
        after = win._browser.cursorForPosition(win._browser.viewport().rect().topLeft()).block().blockNumber()
        check("zoom keeps the top section in place", abs(after - before) <= 1, True)
        win.close()
        app.processEvents()

    # --- window shrink: outline yields first, auto-hides, then returns to pref
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "shrink.md"
        body = "# Doc\n\n" + "\n\n".join(f"## Section {i}\n\n" + "text line\n" * 6 for i in range(20))
        path.write_text(body, encoding="utf-8")
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "shrink_check"))
        win._ensure_overlay()
        win.resize(1100, 500)
        win.show()
        for _ in range(4):
            app.processEvents()

        win._settings.setValue("toc_width", 380)  # a user-dragged outline
        win.resize(1500, 500)
        for _ in range(4):
            app.processEvents()
        check("wide window: outline at the preferred width", abs(win._splitter.sizes()[0] - 380) <= 2, True)
        check("wide window: outline visible", win._toc.isVisible(), True)

        win.resize(520, 500)
        for _ in range(4):
            app.processEvents()
        check("narrow window: outline auto-hides", win._toc.isVisible(), False)
        check("narrow window: preview keeps its hard floor", win._browser.width() >= PREVIEW_HARD, True)
        check("narrow window: outline toggle is disabled", win._toc_btn.isEnabled(), False)

        win.resize(1500, 500)
        for _ in range(4):
            app.processEvents()
        check("widened again: outline returns", win._toc.isVisible(), True)
        check("widened again: returns to the preferred width", abs(win._splitter.sizes()[0] - 380) <= 2, True)
        check("widened again: toggle is enabled", win._toc_btn.isEnabled(), True)

        # edit mode: outline yields first, then the editor, and the preview is
        # protected down to MIN_COLUMN before the editor reaches its hard floor
        win._enter_edit()
        for _ in range(4):
            app.processEvents()
        win.resize(1000, 500)
        for _ in range(4):
            app.processEvents()
        check("edit: preview protected at MIN_COLUMN", win._browser.width() >= MIN_COLUMN - 2, True)
        win.resize(650, 500)
        for _ in range(4):
            app.processEvents()
        # the header row of buttons is ~450 px wide; the editor reaching its hard
        # floor proves that minimum no longer leaks into the splitter
        check("edit: editor reaches its hard floor", win._panel.width() <= EDITOR_HARD + 2, True)
        check("edit: preview never drops below its hard floor", win._browser.width() >= PREVIEW_HARD, True)

        win._confirm_discard = lambda: True
        win._leave_edit()

        win.close()
        app.processEvents()

    # --- mutual highlight is scoped to the section, not the whole document
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "dup.md"
        path.write_text(
            "# Doc\n\n## A\n\nдубль текст тут.\n\n## B\n\nдубль текст тут.\n",
            encoding="utf-8",
        )
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "dup_check"))
        win.resize(900, 600)
        win.show()
        for _ in range(3):
            app.processEvents()
        win._enter_edit()
        for _ in range(3):
            app.processEvents()

        # select the *second* duplicate in the preview (section B)
        preview = win._browser.document()
        second = preview.find("дубль", preview.find("дубль").position() + 1)
        win._browser.setTextCursor(second)
        win._highlight_editor_from_preview()
        layers = win._editor.extraSelections()
        check("scoped mirror produced a highlight", len(layers), 1)
        first_in_editor = win._editor.document().find("дубль").position()
        check("preview mirror lands in the same section, not the first copy",
              layers and layers[0].cursor.selectionStart() > first_in_editor, True)

        # and the reverse: select the second duplicate in the editor
        editor_doc = win._editor.document()
        second_editor = editor_doc.find("дубль", editor_doc.find("дубль").position() + 1)
        win._editor.setTextCursor(second_editor)
        win._highlight_preview_from_editor()
        browser_layers = win._browser.extraSelections()
        preview_first = preview.find("дубль").position()
        preview_mirror = [l for l in browser_layers if l.cursor.selectionStart() > preview_first]
        check("editor mirror lands in the same section too", len(preview_mirror) >= 1, True)

        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- a multi-line preview selection mirrors the whole source span
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "multi.md"
        path.write_text(
            "# D\n\n- alpha one\n- beta two\n- gamma three\n\nEnd.\n",
            encoding="utf-8",
        )
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "multi_check"))
        win.resize(900, 600)
        win.show()
        for _ in range(3):
            app.processEvents()
        win._enter_edit()
        for _ in range(3):
            app.processEvents()
        preview = win._browser.document()
        first = preview.find("alpha")
        last = preview.find("three")
        cursor = QTextCursor(preview)
        cursor.setPosition(first.selectionStart())
        cursor.setPosition(last.selectionEnd(), QTextCursor.MoveMode.KeepAnchor)
        win._browser.setTextCursor(cursor)
        win._highlight_editor_from_preview()
        layers = win._editor.extraSelections()
        check("multi-line selection yields a mirror", len(layers) >= 1, True)
        mirrored = "".join(layer.cursor.selectedText() for layer in layers)
        check("mirror covers every selected line",
              all(word in mirrored for word in ("alpha", "beta", "gamma")), True)
        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- a selection inside a table must not light up the whole table
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "table.md"
        path.write_text(
            "# T\n\n| A | B |\n|---|---|\n| one | two |\n| three | four |\n",
            encoding="utf-8",
        )
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "tbl_check"))
        win.resize(900, 600)
        win.show()
        for _ in range(3):
            app.processEvents()
        win._enter_edit()
        for _ in range(3):
            app.processEvents()
        editor = win._editor.document()
        cursor = QTextCursor(editor)
        cursor.setPosition(editor.find("three").selectionStart())
        cursor.setPosition(editor.find("four").selectionEnd(), QTextCursor.MoveMode.KeepAnchor)
        win._editor.setTextCursor(cursor)
        win._highlight_preview_from_editor()
        text = "".join(sel.cursor.selectedText() for sel in win._browser.extraSelections())
        check("table mirror covers the selected cells", "three" in text and "four" in text, True)
        check("table mirror skips unselected cells", "one" not in text and "two" not in text, True)
        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- tabs: multi-open, dedup and closing share one window
    with tempfile.TemporaryDirectory() as folder:
        tab_a = Path(folder) / "tab_a.md"
        tab_b = Path(folder) / "tab_b.md"
        tab_a.write_text("# A\n", encoding="utf-8")
        tab_b.write_text("# B\n", encoding="utf-8")
        win = viewer_mod.Viewer(tab_a, QSettings(tempfile.mkdtemp(), "tabs_check"))
        win._open_paths([tab_b])
        win.show()
        for _ in range(3):
            app.processEvents()
        check("tabs: two files share one window", win._tabs.count(), 2)
        check("tabs: labels are the file names",
              (win._tabs.tabText(0), win._tabs.tabText(1)), ("tab_a.md", "tab_b.md"))
        check("tabs: drag-reorder is enabled", win._tabs.isMovable(), True)
        check("tabs: close buttons are enabled", win._tabs.tabsClosable(), True)
        win._open_paths([tab_a])  # already open: focus, never duplicate
        check("tabs: reopening focuses the existing tab", win._tabs.count(), 2)
        check("tabs: the existing tab becomes active", win._tabs.currentWidget()._path, tab_a.resolve())
        win._close_tab(1)
        check("tabs: closing one leaves the other", win._tabs.count(), 1)
        win._close_tab(0)
        check("tabs: closing the last empties the window", win._tabs.count(), 0)
        for _ in range(2):
            app.processEvents()

    # --- editor toggles: wrap and line numbers persist and apply at once
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "opts.md"
        path.write_text("# Doc\n\n" + "line\n" * 30, encoding="utf-8")
        settings = QSettings(tempfile.mkdtemp(), "opts_check")
        win = viewer_mod.Viewer(path, settings)
        win.resize(900, 600)
        win.show()
        for _ in range(3):
            app.processEvents()
        win._enter_edit()
        for _ in range(3):
            app.processEvents()

        check("wrap is on by default",
              win._editor.lineWrapMode(), QPlainTextEdit.LineWrapMode.WidgetWidth)
        win._set_editor_wrap(False)
        check("wrap toggle switches to no-wrap",
              win._editor.lineWrapMode(), QPlainTextEdit.LineWrapMode.NoWrap)
        check("wrap choice is saved", settings.value("editor_wrap", True, type=bool), False)
        win._set_editor_wrap(True)
        check("wrap toggle switches back",
              win._editor.lineWrapMode(), QPlainTextEdit.LineWrapMode.WidgetWidth)

        check("line numbers are off by default",
              settings.value("editor_line_numbers", False, type=bool), False)
        check("no gutter while numbers are off",
              win._editor.viewportMargins().left(), 0)
        check("numbers off means not visible", win._editor.line_numbers_visible(), False)
        win._set_editor_line_numbers(True)
        check("line numbers reserve a gutter",
              win._editor.viewportMargins().left() > 0, True)
        check("numbers on are visible", win._editor.line_numbers_visible(), True)
        check("numbers choice is saved",
              settings.value("editor_line_numbers", False, type=bool), True)
        win._set_editor_line_numbers(False)
        check("turning numbers off drops the gutter",
              win._editor.viewportMargins().left(), 0)

        # the custom selection overlay (invariant 22) must keep working with the
        # gutter present: the mirror still lands in the preview and the editor
        # still draws its own layer
        win._set_editor_line_numbers(True)
        editor_cursor = win._editor.textCursor()
        editor_cursor.setPosition(5)
        editor_cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
        win._editor.setTextCursor(editor_cursor)
        win._highlight_preview_from_editor()
        check("mirror still lands in the preview with the gutter",
              len(win._browser.extraSelections()), 1)
        check("editor still draws its own layer with the gutter",
              len(win._editor.extraSelections()), 1)
        win._clear_highlights()

        # a fresh editor picks up the saved choices
        win._set_editor_line_numbers(True)
        win._set_editor_wrap(False)
        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

        win2 = viewer_mod.Viewer(path, settings)
        win2.show()
        for _ in range(3):
            app.processEvents()
        win2._enter_edit()
        for _ in range(3):
            app.processEvents()
        check("new editor picks up the saved wrap",
              win2._editor.lineWrapMode(), QPlainTextEdit.LineWrapMode.NoWrap)
        check("new editor picks up the saved numbers",
              win2._editor.line_numbers_visible(), True)
        win2._confirm_discard = lambda: True
        win2._leave_edit()
        win2.close()
        app.processEvents()

    # --- external file changes: silent reload, dirty prompt, deleted file
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "watch.md"
        body = "# Doc\n\n" + "\n\n".join(f"## Section {i}\n\ntext {i} body\n" for i in range(30))
        path.write_text(body, encoding="utf-8")
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "watch_check"))
        win.resize(800, 400)
        win.show()
        for _ in range(3):
            app.processEvents()

        check("a view watches the file it opened", win._watch, path.resolve())
        check("the watcher really holds the path", str(path.resolve()) in win._watcher.files(), True)

        # scroll into the middle, then change the file *below* the viewport: the
        # reload must pick up the new text and keep the reader in place
        bar = win._browser.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        for _ in range(3):
            app.processEvents()
        before_block = win._browser.cursorForPosition(
            win._browser.viewport().rect().topLeft()
        ).block().blockNumber()
        path.write_text(body + "\n\n## Appended\n\nfresh tail text\n", encoding="utf-8")
        win._reload_timer.stop()
        win._on_file_changed()
        for _ in range(4):
            app.processEvents()
        check("a clean view reloads silently", "fresh tail text" in win._browser.toPlainText(), True)
        after_block = win._browser.cursorForPosition(
            win._browser.viewport().rect().topLeft()
        ).block().blockNumber()
        check("the reload keeps the reading position", abs(after_block - before_block) <= 1, True)
        check("the fresh content is now the disk baseline",
              win._baseline, viewer_mod._normalized(path.read_text(encoding="utf-8")))

        # dirty view: refusing the prompt must not touch the editor or preview
        win._enter_edit()
        for _ in range(3):
            app.processEvents()
        draft = "# Draft\n\nmy unsaved text\n"
        win._editor.setPlainText(draft)
        check("the draft counts as unsaved", win._is_unsaved(), True)
        changed = "# Doc\n\nchanged on disk\n"
        path.write_text(changed, encoding="utf-8")
        original_question = viewer_mod.QMessageBox.question
        try:
            viewer_mod.QMessageBox.question = staticmethod(
                lambda *args, **kwargs: viewer_mod.QMessageBox.StandardButton.No
            )
            win._reload_timer.stop()
            win._on_file_changed()
            for _ in range(2):
                app.processEvents()
            check("refusing keeps the unsaved edit", win._editor.toPlainText(), draft)
            check("refusing leaves the preview alone", "changed on disk" in win._browser.toPlainText(), False)
            check("still marked unsaved after refusing", win._is_unsaved(), True)

            viewer_mod.QMessageBox.question = staticmethod(
                lambda *args, **kwargs: viewer_mod.QMessageBox.StandardButton.Yes
            )
            win._reload_timer.stop()
            win._on_file_changed()
            for _ in range(4):
                app.processEvents()
            check("agreeing reloads the disk text", "changed on disk" in win._browser.toPlainText(), True)
            check("agreeing drops the unsaved state", win._is_unsaved(), False)
            check("the editor shows the disk text again", win._editor.toPlainText(), changed)
        finally:
            viewer_mod.QMessageBox.question = original_question

        # a vanished file must not crash and must drop its watch, leaving the
        # shown document exactly as it was
        path.unlink()
        win._reload_timer.stop()
        win._on_file_changed()  # must be a no-op, not an exception
        app.processEvents()
        check("a deleted file is dropped from the watch",
              str(path.resolve()) in win._watcher.files(), False)
        check("a deleted file leaves the content alone",
              "changed on disk" in win._browser.toPlainText(), True)

        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- search & replace in the editor (Ctrl+H)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "replace.md"
        path.write_text("# Doc\n\nalpha one\nalpha two\nalpha three\n", encoding="utf-8")
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "replace_check"))
        win.resize(900, 600)
        win.show()
        for _ in range(3):
            app.processEvents()

        check("replace row starts hidden", win._replace_row.isVisible(), False)
        win._toggle_replace()
        for _ in range(3):
            app.processEvents()
        check("Ctrl+H enters edit mode", win._editing(), True)
        check("Ctrl+H reveals the replace row", win._replace_row.isVisible(), True)
        check("Ctrl+H sets replace mode", win._replace_mode, True)

        # an empty pattern must be a no-op (both buttons)
        win._find_edit.clear()
        win._replace_edit.setText("omega")
        check("empty pattern replaces nothing", win._replace_all(), 0)
        win._replace_next()  # must not raise and must not edit
        check("empty pattern leaves the text alone",
              win._editor.toPlainText().count("omega"), 0)

        # replace one: exactly one occurrence changes, search moves on
        win._find_edit.setText("alpha")
        app.processEvents()
        win._find_first()
        win._replace_edit.setText("beta")
        win._replace_next()
        check("replace changes exactly one occurrence",
              win._editor.toPlainText().count("beta"), 1)
        check("replace leaves the other matches",
              win._editor.toPlainText().count("alpha"), 2)
        check("replace selects the next match",
              win._editor.textCursor().selectedText(), "alpha")
        check("replace marks the editor dirty", win._is_unsaved(), True)
        check("replace does not rerender the preview",
              "beta" in win._browser.toPlainText(), False)

        # replace all: changes N and reports it
        count = win._replace_all()
        check("replace-all changes every remaining match", count, 2)
        check("replace-all leaves no match behind",
              win._editor.toPlainText().count("alpha"), 0)
        check("replace-all produces the right number",
              win._editor.toPlainText().count("beta"), 3)
        check("replace-all keeps the editor dirty", win._is_unsaved(), True)
        check("replace-all keeps the preview untouched",
              "beta" in win._browser.toPlainText(), False)

        # Ctrl+F goes back to the plain search bar
        win._toggle_find()
        check("Ctrl+F hides the replace row", win._replace_row.isVisible(), False)
        check("Ctrl+F clears replace mode", win._replace_mode, False)

        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- editor <-> preview scroll sync while editing
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "scrollsync.md"
        body = "# Doc\n\n" + "\n\n".join(
            f"## Section {i}\n\n" + "text line\n" * 8 for i in range(40)
        )
        path.write_text(body, encoding="utf-8")
        settings = QSettings(tempfile.mkdtemp(), "scroll_sync")
        win = viewer_mod.Viewer(path, settings)
        win.resize(1000, 500)
        win.show()
        for _ in range(3):
            app.processEvents()
        check("scroll sync is on by default",
              settings.value("sync_scroll", True, type=bool), True)

        win._enter_edit()
        for _ in range(4):
            app.processEvents()
        win._release_scroll_sync()  # let the settled mode accept user scrolls

        # preview -> editor
        win._sync_count = 0
        preview_bar = win._browser.verticalScrollBar()
        preview_bar.setValue(preview_bar.maximum() // 2)
        app.processEvents()
        check("a preview scroll queues a sync", win._sync_source is win._browser, True)
        win._sync_scroll_now()
        check("a preview scroll moves the editor",
              win._editor.verticalScrollBar().value() > 0, True)
        check("one user scroll applies one sync", win._sync_count, 1)
        check("no loop is left pending", win._sync_source, None)

        # editor -> preview
        win._sync_count = 0
        editor_bar = win._editor.verticalScrollBar()
        editor_bar.setValue(max(0, editor_bar.value() - editor_bar.pageStep()))
        app.processEvents()
        check("an editor scroll queues a sync", win._sync_source is win._editor, True)
        preview_before = win._browser.verticalScrollBar().value()
        win._sync_scroll_now()
        check("an editor scroll moves the preview",
              win._browser.verticalScrollBar().value() != preview_before, True)
        check("the reverse sync also fires once", win._sync_count, 1)
        check("the reverse sync leaves no pending loop", win._sync_source, None)

        # the guard really breaks the loop: a sync-driven scroll re-queues nothing
        win._syncing_scroll = True
        win._browser.verticalScrollBar().setValue(10)
        app.processEvents()
        check("a sync-driven scroll does not queue another sync",
              win._sync_source, None)
        win._syncing_scroll = False

        # disabled toggle: scrolling one pane leaves the other alone
        win._set_sync_scroll(False)
        check("sync toggle is persisted",
              settings.value("sync_scroll", True, type=bool), False)
        editor_bar = win._editor.verticalScrollBar()
        editor_bar.setValue(
            min(editor_bar.maximum(), editor_bar.value() + editor_bar.pageStep())
        )
        app.processEvents()
        check("a disabled sync queues nothing", win._sync_source, None)
        preview_now = win._browser.verticalScrollBar().value()
        win._sync_scroll_now()
        check("a disabled sync does not move the preview",
              win._browser.verticalScrollBar().value(), preview_now)
        win._set_sync_scroll(True)

        # entering edit must not disturb the preview position (invariant 15/17)
        win._confirm_discard = lambda: True
        win._leave_edit()
        for _ in range(3):
            app.processEvents()
        anchor_block = win._browser.cursorForPosition(
            win._browser.viewport().rect().topLeft()
        ).block().blockNumber()
        win._enter_edit()
        for _ in range(4):
            app.processEvents()
        check("entering edit leaves the preview position alone",
              win._browser.cursorForPosition(
                  win._browser.viewport().rect().topLeft()
              ).block().blockNumber(), anchor_block)

        win._confirm_discard = lambda: True
        win._leave_edit()
        win.close()
        app.processEvents()

    # --- export: standalone HTML, PDF via QPrinter, cancel writes nothing
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "export.md"
        path.write_text(
            "# Заголовок\n\nтекст для экспорта\n\n## Раздел\n\nтело\n",
            encoding="utf-8",
        )
        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "export_check"))
        win.resize(800, 600)
        win.show()
        for _ in range(3):
            app.processEvents()

        original_save = viewer_mod.QFileDialog.getSaveFileName

        # HTML: the file is standalone, carries the heading, the title (file
        # name), the charset and the active theme's CSS
        html_dest = Path(folder) / "out.html"
        viewer_mod.QFileDialog.getSaveFileName = staticmethod(
            lambda *args, **kwargs: (str(html_dest), "HTML (*.html)")
        )
        try:
            ok = win.export_via_dialog("html")
        finally:
            viewer_mod.QFileDialog.getSaveFileName = original_save
        check("HTML export reports success", ok, True)
        check("HTML export wrote a file", html_dest.is_file(), True)
        html = html_dest.read_text(encoding="utf-8")
        check("HTML export carries the document heading", "Заголовок" in html, True)
        check("HTML export title is the file name",
              "<title>export.md</title>" in html, True)
        check("HTML export declares utf-8", '<meta charset="utf-8">' in html, True)
        check("HTML export embeds a style block", "<style>" in html, True)
        check("HTML export embeds the dark theme CSS",
              theme_mod.palette("dark")["PAGE_BG"] in html, True)
        check("HTML export keeps links/resource URLs local",
              "http://" not in html and "https://" not in html, True)

        # the light theme must be reflected in the exported page too
        win._set_theme("light")
        app.processEvents()
        light_dest = Path(folder) / "light.html"
        viewer_mod.QFileDialog.getSaveFileName = staticmethod(
            lambda *args, **kwargs: (str(light_dest), "HTML (*.html)")
        )
        try:
            win.export_via_dialog("html")
        finally:
            viewer_mod.QFileDialog.getSaveFileName = original_save
        check("HTML export follows the active theme",
              theme_mod.palette("light")["PAGE_BG"]
              in light_dest.read_text(encoding="utf-8"), True)
        win._set_theme("dark")
        app.processEvents()

        # PDF: a real, non-empty file written without any printer
        pdf_dest = Path(folder) / "out.pdf"
        viewer_mod.QFileDialog.getSaveFileName = staticmethod(
            lambda *args, **kwargs: (str(pdf_dest), "PDF (*.pdf)")
        )
        try:
            ok = win.export_via_dialog("pdf")
        finally:
            viewer_mod.QFileDialog.getSaveFileName = original_save
        check("PDF export reports success", ok, True)
        check("PDF export wrote a non-empty file",
              pdf_dest.is_file() and pdf_dest.stat().st_size > 0, True)
        check("PDF export is a real PDF",
              pdf_dest.read_bytes()[:5], b"%PDF-")

        # cancelling the dialog must write nothing at all
        before = sorted(p.name for p in Path(folder).iterdir())
        viewer_mod.QFileDialog.getSaveFileName = staticmethod(
            lambda *args, **kwargs: ("", "")
        )
        try:
            cancelled_html = win.export_via_dialog("html")
            cancelled_pdf = win.export_via_dialog("pdf")
        finally:
            viewer_mod.QFileDialog.getSaveFileName = original_save
        check("cancelled HTML export reports no success", cancelled_html, False)
        check("cancelled PDF export reports no success", cancelled_pdf, False)
        check("cancelling writes no file",
              sorted(p.name for p in Path(folder).iterdir()), before)

        # an extension-less destination still gets the right suffix
        bare = Path(folder) / "bare"
        viewer_mod.QFileDialog.getSaveFileName = staticmethod(
            lambda *args, **kwargs: (str(bare), "HTML (*.html)")
        )
        try:
            win.export_via_dialog("html")
        finally:
            viewer_mod.QFileDialog.getSaveFileName = original_save
        check("a missing suffix is added", (Path(folder) / "bare.html").is_file(), True)

        win.close()
        app.processEvents()

    print("all checks passed" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    code = main()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    # Qt tears down badly at interpreter exit (0xC0000409) once widgets outlive
    # the temp folders they point at, so skip finalisation deliberately
    sys.stdout.flush()
    os._exit(code)