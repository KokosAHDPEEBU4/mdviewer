"""Drives the tab shell without a user: open -> close -> reorder -> tear-off /
merge -> session.

Run from the project root:
    .venv\\Scripts\\python tools\\tabs_check.py

The real drag path (`QDrag`/OLE) is synchronous and needs a live pointer, so it
is never launched here. The primitives it would call (`_take_view`,
`_adopt_view`, `_tear_off`, `TabBar.dropEvent`, `_resolve_drag_view`) are driven
directly instead, which is deterministic and still covers the tab logic.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QByteArray, QMimeData, QPoint, QPointF, QSettings, Qt
from PyQt6.QtGui import QDropEvent, QShortcut
from PyQt6.QtWidgets import QApplication

import viewer as viewer_mod
import instance
from main import create_app
from viewer import (
    MainWindow,
    _TabMimeData,
    _resolve_drag_view,
    _resolve_tab_drop,
    _window_at,
)

failures = []


def check(label, got, want):
    ok = got == want
    failures.append(label) if not ok else None
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (want {want!r})"))


def pump(app, rounds: int = 4) -> None:
    for _ in range(rounds):
        app.processEvents()


def settings_for(tag: str) -> QSettings:
    return QSettings(tempfile.mkdtemp(), tag)


def close_window(win: MainWindow | None) -> None:
    """Close a window without ever opening the unsaved-changes dialog."""
    if win is None:
        return
    for index in range(win._tabs.count()):
        view = win._tabs.widget(index)
        if view is not None:
            view._confirm_discard = lambda: True
    win.close()


def _install_test_screen_placement() -> None:
    """Optionally keep the automated test windows off the primary monitor.

    Set `MDVIEWER_TEST_SCREEN=secondary` (or a screen name) to place every
    `MainWindow` on that screen; otherwise nothing changes.
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
    original_show = viewer_mod.MainWindow.show

    def show_on_target(self):
        size = self.size()
        self.setGeometry(geo.x() + 20, geo.y() + 20, size.width(), size.height())
        original_show(self)

    viewer_mod.MainWindow.show = show_on_target


def main() -> int:
    app = create_app()
    _install_test_screen_placement()

    with tempfile.TemporaryDirectory() as folder:
        a = Path(folder) / "a.md"
        b = Path(folder) / "b.md"
        a.write_text("# A\r\n\r\nalpha body\r\n", encoding="utf-8")
        b.write_text("# B\r\n\r\nbeta body\r\n", encoding="utf-8")

        # --- 1. CLI semantics: one window, one tab per file
        win = MainWindow(settings_for("tabs_cli"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        check("one window hosts the CLI open", viewer_mod._live_windows() == [win], True)
        check("two files become two tabs", win._tabs.count(), 2)
        check("tab labels are the file names",
              (win._tabs.tabText(0), win._tabs.tabText(1)), ("a.md", "b.md"))
        check("tab tooltips are the full paths",
              (win._tabs.tabToolTip(0), win._tabs.tabToolTip(1)),
              (str(a.resolve()), str(b.resolve())))
        check("the last opened file is active", win._tabs.currentWidget()._path, b.resolve())

        # --- 2. dedup by resolved path
        win._open_paths([a])
        pump(app)
        check("reopening a file does not add a tab", win._tabs.count(), 2)
        check("reopening focuses the existing tab", win._tabs.currentWidget()._path, a.resolve())
        win._open_paths([Path(folder) / "." / "a.md"])
        check("dedup compares resolved paths", win._tabs.count(), 2)

        # --- 3. closing tabs
        win._close_tab(0)
        pump(app)
        check("closing one tab leaves one", win._tabs.count(), 1)
        check("the surviving tab becomes active", win._tabs.currentWidget()._path, b.resolve())
        win._close_tab(0)
        pump(app)
        check("closing the last tab empties the window", win._tabs.count(), 0)
        check("the emptied window is gone", win not in viewer_mod._live_windows(), True)

        # --- 4. reordering and persisted order
        win = MainWindow(settings_for("tabs_order"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        check("tabs are movable", win._tabs.isMovable(), True)
        check("tabs have close buttons", win._tabs.tabsClosable(), True)
        check("order starts a, b",
              [win._tabs.widget(i)._path for i in range(2)], [a.resolve(), b.resolve()])
        win._tabs.tabBar().moveTab(0, 1)
        pump(app)
        check("moveTab swaps the pages",
              [win._tabs.widget(i)._path for i in range(2)], [b.resolve(), a.resolve()])
        check("tab_order follows the new order",
              win._settings.value("tab_order", [], type=list),
              [str(b.resolve()), str(a.resolve())])
        close_window(win)
        pump(app)

        # --- 5. tear-off moves the very same view into a fresh window
        win = MainWindow(settings_for("tabs_tearoff"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        view = win._tabs.widget(0)
        view._set_zoom(3)
        marker = id(view)
        win._tear_off(view, QPoint(100, 100))
        pump(app)
        fresh = [w for w in viewer_mod._live_windows() if w is not win]
        check("tear-off creates a second window", len(fresh), 1)
        new = fresh[0]
        check("the moved view is the same object", new._tabs.widget(0) is view, True)
        check("the moved view kept its identity", id(new._tabs.widget(0)), marker)
        check("the moved view kept its file", view._path, a.resolve())
        check("the moved view kept its zoom", view._zoom, 3)
        check("the source window keeps its remaining tab", win._tabs.count(), 1)
        close_window(new)
        close_window(win)
        pump(app)

        # a tear-off of the last tab must not leave an empty source behind
        solo = MainWindow(settings_for("tabs_solo"))
        solo._open_paths([a])
        solo.show()
        pump(app)
        only = solo._tabs.widget(0)
        solo._tear_off(only, QPoint(60, 60))
        pump(app)
        check("the emptied source closes on tear-off",
              solo not in viewer_mod._live_windows(), True)
        check("the sole tab landed in another window",
              only.window() is not None and only.window() is not solo, True)
        close_window(only.window())
        pump(app)

        # --- 6. merge: take/adopt keeps the view and re-hooks its signals
        source = MainWindow(settings_for("tabs_merge_src"))
        target = MainWindow(settings_for("tabs_merge_dst"))
        source._open_paths([a])
        target._open_paths([b])
        target.show()
        pump(app)
        view = source._tabs.widget(0)
        source._take_view(view)
        check("take_view detaches without deleting", source._tabs.count(), 0)
        target._adopt_view(view)
        check("adopt_view hosts the widget", target._tabs.indexOf(view), 1)
        check("the adopted widget is the same object", target._tabs.widget(1) is view, True)
        check("the view now belongs to the target", view.window() is target, True)
        # a dirty change must refresh the *target* label and title
        view._enter_edit()
        view._editor.setPlainText("# A changed\r\n")
        pump(app)
        merged = target._tabs.indexOf(view)
        check("the merged tab label tracks its dirty state",
              " •" in target._tabs.tabText(merged), True)
        check("the target title carries the bullet", "•" in target.windowTitle(), True)
        view._confirm_discard = lambda: True
        view._leave_edit()
        close_window(source)
        close_window(target)
        pump(app)

        # merge through the real drop handler: source closes when emptied
        source = MainWindow(settings_for("tabs_drop_src"))
        target = MainWindow(settings_for("tabs_drop_dst"))
        source._open_paths([a])
        target._open_paths([b])
        target.show()
        pump(app)
        view = source._tabs.widget(0)
        mime = _TabMimeData(view)
        mime.setData(viewer_mod.TAB_MIME, QByteArray(b"tab"))
        drop = QDropEvent(
            QPointF(5, 5), Qt.DropAction.MoveAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        target._tabs.tabBar().dropEvent(drop)
        pump(app)
        check("drop handler merges the tab into the target", target._tabs.indexOf(view), 0)
        check("drop handler closes the emptied source",
              source not in viewer_mod._live_windows(), True)
        close_window(target)
        pump(app)

        # --- 7. drag payload resolution (both channels)
        win = MainWindow(settings_for("tabs_resolve"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        first = win._tabs.widget(0)
        second = win._tabs.widget(1)
        payload = _TabMimeData(first)
        check("the payload view resolves from the mimedata",
              _resolve_drag_view(payload) is first, True)
        viewer_mod._DRAG_VIEW = second
        check("a bare mimedata falls back to the drag global",
              _resolve_drag_view(QMimeData()) is second, True)
        check("the payload still wins over the global",
              _resolve_drag_view(payload) is first, True)
        viewer_mod._DRAG_VIEW = None
        check("no channel resolves to None", _resolve_drag_view(QMimeData()), None)
        close_window(win)
        pump(app)

        # --- 8. shortcut scoping: per-doc keys are widget-scoped, no clashes
        win = MainWindow(settings_for("tabs_keys"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        view = win._tabs.currentWidget()
        doc_shortcuts = view.findChildren(QShortcut)
        check("every per-document shortcut is widget-scoped",
              all(s.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
                  for s in doc_shortcuts), True)
        check("the document registers its keys", len(doc_shortcuts) > 0, True)
        window_shortcuts = [s for s in win.findChildren(QShortcut) if s.parent() is win]
        window_keys = [s.key().toString() for s in window_shortcuts]
        doc_keys = [s.key().toString() for s in doc_shortcuts]
        check("window keys are unique", len(window_keys), len(set(window_keys)))
        check("Ctrl+W is a window key", "Ctrl+W" in window_keys, True)
        check("no document key shadows a window key",
              sorted(set(window_keys) & set(doc_keys)), [])
        close_window(win)
        pump(app)

        # --- 9. session save / restore
        session = QSettings(tempfile.mkdtemp(), "tabs_session")
        session.clear()
        session.setValue("restore_session", True)
        win = MainWindow(session)
        win._open_paths([a, b])
        win.show()
        pump(app)
        win._save_session()
        check("session records both files",
              session.value("session_paths", [], type=list),
              [str(a.resolve()), str(b.resolve())])
        check("session records the active tab",
              session.value("session_active", "", type=str), str(b.resolve()))
        close_window(win)
        pump(app)
        restored = MainWindow(session)
        check("restore reports success", restored._restore_session(), True)
        check("restore reopens both tabs", restored._tabs.count(), 2)
        check("restore keeps the tab order",
              [restored._tabs.widget(i)._path for i in range(restored._tabs.count())],
              [a.resolve(), b.resolve()])
        check("restore keeps the active tab", restored._tabs.currentWidget()._path, b.resolve())
        close_window(restored)
        pump(app)

        # a disabled session toggle is never written
        plain = QSettings(tempfile.mkdtemp(), "tabs_nosession")
        plain.clear()
        win = MainWindow(plain)
        win._open_paths([a, b])
        win._save_session()
        check("no session is written while disabled", plain.contains("session_paths"), False)
        close_window(win)
        pump(app)

        # --- 10. per-tab isolation: zoom, edit mode and history do not leak
        win = MainWindow(settings_for("tabs_isolation"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        first = win._tabs.widget(0)
        second = win._tabs.widget(1)
        first._set_zoom(4)
        check("zoom stays on its own tab", second._zoom, 0)
        check("the other tab builds no editor yet", second._editor, None)
        first._enter_edit()
        pump(app)
        check("edit mode stays on its own tab", first._editing(), True)
        check("the other tab is not editing", second._editing(), False)
        check("the other tab's editor zoom is untouched", second._editor_zoom, 0)
        check("history lists are independent", first._history is not second._history, True)
        first._history.append(Path(folder) / "ghost.md")
        check("appending to one history leaves the other empty", len(second._history), 0)
        check("highlight layers are independent", first._hl_layers is not second._hl_layers, True)
        first._confirm_discard = lambda: True
        first._leave_edit()
        close_window(win)
        pump(app)

        # --- 11. a dropped tab is resolved by the window under the pointer
        left = MainWindow(settings_for("tabs_drop_l"))
        right = MainWindow(settings_for("tabs_drop_r"))
        left._open_paths([a, b])
        left.setGeometry(100, 100, 300, 200)
        right.setGeometry(500, 100, 300, 200)
        left.show()
        right.show()
        pump(app)
        left.setGeometry(100, 100, 300, 200)
        right.setGeometry(500, 100, 300, 200)
        pump(app)
        inside_left = QPoint(200, 150)
        inside_right = QPoint(600, 150)
        far_away = QPoint(4000, 4000)
        check("window-at finds the left window", _window_at(inside_left) is left, True)
        check("window-at finds the right window", _window_at(inside_right) is right, True)
        check("window-at is empty away from every window", _window_at(far_away) is None, True)

        merged = left._tabs.widget(0)
        check("the merged view starts in the source", left._tabs.indexOf(merged) >= 0, True)
        _resolve_tab_drop(left, merged, inside_right)
        pump(app)
        check("dropping on another window merges the tab", right._tabs.indexOf(merged) >= 0, True)
        check("the merged view left the source window", left._tabs.indexOf(merged), -1)
        check("the merged view kept its file", merged._path, a.resolve())
        before_idx = right._tabs.indexOf(merged)
        _resolve_tab_drop(right, merged, inside_right)  # drop back on the source
        check("dropping on the source window keeps the tab", right._tabs.indexOf(merged), before_idx)

        torn = left._tabs.widget(0)
        _resolve_tab_drop(left, torn, far_away)
        pump(app)
        check("dropping away from windows tears the tab off",
              left not in viewer_mod._live_windows(), True)
        check("the torn-off view lives in a fresh window",
              torn.window() is not None and torn.window() is not left, True)
        close_window(torn.window())
        close_window(right)
        pump(app)

        # --- 12. single-instance hand-off queue
        state = Path(folder) / "state"
        os.environ["MDVIEWER_STATE_DIR"] = str(state)
        try:
            instance.write_request([a, b])
            instance.write_request([])
            check("queued requests arrive oldest first",
                  instance.take_requests(), [[str(a), str(b)], []])
            check("the queue is drained after reading", instance.take_requests(), [])
        finally:
            os.environ.pop("MDVIEWER_STATE_DIR", None)

        # --- 13. reopen closed tab, zoom-label toggle, default zoom
        viewer_mod._CLOSED_TABS.clear()
        win = MainWindow(settings_for("tabs_reopen"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        window_keys = [s.key().toString() for s in win.findChildren(QShortcut)
                       if s.parent() is win]
        check("Ctrl+Shift+T is a window key", "Ctrl+Shift+T" in window_keys, True)
        win._close_tab(1)
        pump(app)
        check("a closed tab is remembered", viewer_mod._CLOSED_TABS[-1], b.resolve())
        win._reopen_closed_tab()
        pump(app)
        check("reopen restores the tab", win._tabs.count(), 2)
        check("the restored tab is the closed file",
              win._tabs.currentWidget()._path, b.resolve())
        check("reopening consumes the stack entry", len(viewer_mod._CLOSED_TABS), 0)

        view = win._tabs.currentWidget()
        view._ensure_overlay()
        pump(app)
        check("zoom label starts at 100%", view._zoom_label.text(), "100%")
        view._zoom_label.click()
        pump(app)
        check("clicking 100% jumps to 200%", view._zoom, 10)
        check("the label shows 200%", view._zoom_label.text(), "200%")
        view._zoom_label.click()
        pump(app)
        check("clicking 200% returns to 100%", view._zoom, 0)

        (Path(folder) / "c.md").write_text("# C\r\n\r\ngamma\r\n", encoding="utf-8")
        win._settings.setValue("default_zoom", 2)  # 120 %
        fresh = win._add_tab(Path(folder) / "c.md")
        check("a new tab opens at the default zoom", fresh._zoom, 2)
        win._settings.setValue("default_zoom", 0)
        close_window(win)
        pump(app)

        # --- 14. reading mode (F11): fullscreen, tab bar and outline hidden
        win = MainWindow(settings_for("tabs_reading"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        window_keys = [s.key().toString() for s in win.findChildren(QShortcut)
                       if s.parent() is win]
        check("F11 is a window key", "F11" in window_keys, True)
        view = win._tabs.currentWidget()
        check("not fullscreen before F11", win.isFullScreen(), False)
        check("tab bar visible before F11", win._tabs.tabBar().isVisible(), True)
        check("outline visible before F11", view._toc_panel.isVisible(), True)
        win._toggle_reading_mode()
        pump(app)
        check("F11 enters fullscreen", win.isFullScreen(), True)
        check("F11 hides the tab bar", win._tabs.tabBar().isVisible(), False)
        check("reading mode hides the outline", view._toc_panel.isVisible(), False)
        view._handle_esc()
        pump(app)
        check("Esc leaves reading mode", win.isFullScreen(), False)
        check("Esc brings the tab bar back", win._tabs.tabBar().isVisible(), True)
        check("Esc brings the outline back", view._toc_panel.isVisible(), True)
        close_window(win)
        pump(app)

        # --- 15. recent files: newest first, deduped, capped, existing only
        recent = QSettings(tempfile.mkdtemp(), "tabs_recent")
        recent.clear()
        r1 = Path(folder) / "r1.md"
        r2 = Path(folder) / "r2.md"
        r1.write_text("# R1\n", encoding="utf-8")
        r2.write_text("# R2\n", encoding="utf-8")
        check("recents start empty", viewer_mod.recent_files(recent), [])
        viewer_mod.remember_recent_file(r1, recent)
        viewer_mod.remember_recent_file(r2, recent)
        check("recents keep the newest first",
              viewer_mod.recent_files(recent), [str(r2.resolve()), str(r1.resolve())])
        viewer_mod.remember_recent_file(r1, recent)
        check("re-remembering dedups and moves to the front",
              viewer_mod.recent_files(recent), [str(r1.resolve()), str(r2.resolve())])
        for i in range(14):
            extra = Path(folder) / f"extra{i}.md"
            extra.write_text("# X\n", encoding="utf-8")
            viewer_mod.remember_recent_file(extra, recent)
        check("recents are capped at the limit",
              len(viewer_mod.recent_files(recent)), viewer_mod.RECENT_LIMIT)
        check("the cap keeps the newest and drops the oldest",
              viewer_mod.recent_files(recent)[0], str((Path(folder) / "extra13.md").resolve()))
        gone = Path(folder) / "gone.md"
        gone.write_text("# G\n", encoding="utf-8")
        viewer_mod.remember_recent_file(gone, recent)
        gone.unlink()
        check("a deleted file drops out of the recents",
              str(gone.resolve()) in viewer_mod.recent_files(recent), False)

        # --- 16. "copy path" / recent reopen act on real files
        win = MainWindow(settings_for("tabs_paths"))
        win._open_paths([a, b])
        win.show()
        pump(app)
        check("opening files records them as recent",
              viewer_mod.recent_files(win._settings)[0], str(b.resolve()))
        win._copy_path(win._tabs.widget(1)._path)
        check("copy path puts the file path on the clipboard",
              QApplication.clipboard().text(), str(b.resolve()))
        win._open_recent(str(a.resolve()))
        check("reopening a recent focuses its existing tab",
              win._tabs.currentWidget()._path, a.resolve())
        check("reopening a recent promotes it to the front",
              viewer_mod.recent_files(win._settings)[0], str(a.resolve()))
        win._clear_recent()
        check("clearing the recents empties the list",
              viewer_mod.recent_files(win._settings), [])
        close_window(win)
        pump(app)

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
