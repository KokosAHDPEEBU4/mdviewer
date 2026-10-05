"""Drives the edit mode without a user: open -> edit -> convert -> save.

Run from the project root:
    .venv\\Scripts\\python tools\\edit_check.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt, QUrl
from PyQt6.QtGui import QMouseEvent, QPalette, QTextCursor, QWheelEvent
from PyQt6.QtWidgets import QApplication, QTextBrowser

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

        # follow the link exactly as a click would
        win._browser.anchorClicked.emit(QUrl.fromLocalFile(str(second)))
        for _ in range(3):
            app.processEvents()
        check("linked file opened in the same window", win._path, second.resolve())
        check("linked file rendered with the theme", "тело второго." in win._browser.toPlainText(), True)
        check("outline rebuilt for the linked file", win._toc.count(), 2)
        check("back is enabled after following a link", win._back_btn.isEnabled(), True)

        win._go_back()
        for _ in range(3):
            app.processEvents()
        check("back returns to the previous file", win._path, first.resolve())
        check("previous file is rendered again", "Смотри" in win._browser.toPlainText(), True)
        check("back is disabled once history is empty", win._back_btn.isEnabled(), False)

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