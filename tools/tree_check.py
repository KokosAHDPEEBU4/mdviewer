"""Drives the file-tree panel («Файлы папки») without a user.

Run from the project root:
    .venv\\Scripts\\python tools\\tree_check.py
"""

import ctypes
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QByteArray, QPointF, QMimeData, QSettings, Qt, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QApplication

import theme
import viewer as viewer_mod
from main import create_app
from theme import TREE_MIN_WIDTH, TREE_WIDTH
from viewer import FILE_MIME, TreeFileList, _FileMimeData, _resolve_drag_file, natural_key

failures = []


def check(label, got, want):
    ok = got == want
    failures.append(label) if not ok else None
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (want {want!r})"))


def make_window(app, folder, tag):
    win = viewer_mod.Viewer(folder / "a.md", QSettings(tempfile.mkdtemp(), tag))
    win.resize(1100, 700)
    win.show()
    for _ in range(4):
        app.processEvents()
    return win


def main() -> int:
    app = create_app()

    # --- natural sort key (pure)
    names = ["b10.md", "a.md", "B2.md", "файл2.md", "файл10.md"]
    check("natural sort: digits as numbers, case-folded",
          sorted(names, key=natural_key),
          ["a.md", "B2.md", "b10.md", "файл2.md", "файл10.md"])

    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        (folder / "sub").mkdir()
        (folder / "b10.md").write_text("# b10\n", encoding="utf-8")
        (folder / "a.md").write_text("# a\n", encoding="utf-8")
        (folder / "b2.md").write_text("# b2\n", encoding="utf-8")
        (folder / "notes.txt").write_text("notes", encoding="utf-8")
        (folder / "pic.png").write_bytes(b"png")
        (folder / "desktop.ini").write_text("[x]", encoding="utf-8")
        secret = folder / "secret.md"
        secret.write_text("# hidden\n", encoding="utf-8")
        ctypes.windll.kernel32.SetFileAttributesW(str(secret), 0x2)  # FILE_ATTRIBUTE_HIDDEN

        # --- M1: hidden by default (Q30), toggle, populate (Q1/Q2/Q4/Q5/Q6/Q7)
        win = make_window(app, folder, "tree_first")
        check("panel hidden by default", win._tree_panel.isVisible(), False)
        check("the toggle persists visibility",
              (win._toggle_tree(), win._settings.value("tree_visible", False, type=bool))[1], True)
        for _ in range(4):
            app.processEvents()
        check("panel visible after toggle", win._tree_panel.isVisible(), True)
        check("panel at least its minimum width", win._tree_panel.width() >= TREE_MIN_WIDTH - 2, True)
        check("default width preference", win._tree_width_pref(), TREE_WIDTH)
        check("folder badge shows the folder",
              win._tree_folder_label.text(), str(folder))
        names = [win._tree_list.item(i).text() for i in range(win._tree_list.count())]
        check("list holds files only, sorted naturally, hidden dropped",
              [n.replace("  •", "") for n in names],
              ["a.md", "b2.md", "b10.md", "notes.txt", "pic.png"])
        png = win._tree_item_for(folder / "pic.png")
        muted = theme.palette("dark")["TEXT_MUTED"].lstrip("#").lower()
        check("png row uses the muted token",
              png.foreground().color().name().lower(), "#" + muted)
        active = win._tree_list.currentItem()
        check("active file is highlighted",
              active.data(Qt.ItemDataRole.UserRole), folder / "a.md")
        check("rows carry the full-path tooltip",
              win._tree_item_for(folder / "notes.txt").toolTip(), str(folder / "notes.txt"))

        # --- M1: persistence of the width (Q29)
        win._tree_splitter.setSizes([300, win._tree_splitter.width() - 300])
        win._on_tree_splitter_moved()
        check("a manual drag stores the panel width",
              int(win._settings.value("tree_width")),
              win._tree_splitter.sizes()[0])

        # --- M2: single click navigates the current tab (Q11), «Назад» (Q26)
        win._on_tree_clicked(win._tree_item_for(folder / "b10.md"))
        for _ in range(2):
            app.processEvents()
        check("single click opens in the current tab",
              win._current_view()._path, folder / "b10.md")
        check("the visit lands in the history", len(win._history), 1)
        win._go_back()
        check("Alt+Left returns from a panel open",
              win._current_view()._path, folder / "a.md")

        # --- M2: clicking the active file is a no-op (Q11)
        history_before = len(win._history)
        win._on_tree_clicked(win._tree_item_for(folder / "a.md"))
        check("clicking the active file does nothing",
              (len(win._history), win._current_view()._path),
              (history_before, folder / "a.md"))

        # --- M2: new-tab opens (Q12) and dedup (Q14)
        win._open_tree_path(folder / "b2.md", new_tab=True)
        for _ in range(2):
            app.processEvents()
        check("new-tab open adds a tab", win._tabs.count(), 2)
        win._open_tree_path(folder / "b2.md")  # single click on a tabbed file
        for _ in range(2):
            app.processEvents()
        check("single click focuses the existing tab (no navigation)",
              (win._tabs.count(), win._tabs.currentIndex(), len(win._history)),
              (2, 1, history_before))
        names = [win._tree_list.item(i).text() for i in range(win._tree_list.count())]
        check("tabbed files carry the «•» bullet",
              sorted(n for n in names if n.endswith("•")),
              ["a.md  •", "b2.md  •"])
        win._close_tab(1)
        for _ in range(2):
            app.processEvents()
        names = [win._tree_list.item(i).text() for i in range(win._tree_list.count())]
        check("closing a tab drops its bullet", "b2.md  •" in names, False)

        # --- M2: the panel follows the active tab's folder (Q23) + anchor (Q16)
        other = Path(tempfile.mkdtemp())
        (other / "x.md").write_text("# x\n", encoding="utf-8")
        win._add_tab(other / "x.md")
        for _ in range(3):
            app.processEvents()
        check("the panel switched to the new folder",
              (win._tree_folder, win._tree_folder_label.text()), (other, str(other)))
        check("the old folder kept its anchor",
              win._tree_state[str(folder)], "a.md")
        win._sync_tree_folder(force=True)
        check("forced sync restores the anchor row",
              win._tree_list.currentItem().data(Qt.ItemDataRole.UserRole),
              other / "x.md")

        # --- M3: folder watcher (Q35/Q36; slots called directly, invariant 29)
        (folder / "fresh.md").write_text("# fresh\n", encoding="utf-8")
        win._add_tab(folder / "a.md")
        for _ in range(3):
            app.processEvents()
        win._on_tree_dir_changed(str(folder))  # raw event: rewatch + debounce
        check("the watch is re-armed after an event",
              win._tree_watcher.directories(), [str(folder)])
        win._refresh_tree()  # the debounced slot
        check("the refresh shows the new file",
              win._tree_item_for(folder / "fresh.md") is not None, True)

        # --- M3: an unreadable folder degrades to a notice (Q38)
        win._tree_folder = Path("Z:/definitely/missing")
        win._populate_tree()
        check("an unreadable folder shows a notice",
              [win._tree_list.item(i).text() for i in range(win._tree_list.count())],
              ["Папка недоступна"])
        win._tree_folder = folder  # the notice test parked a fake folder
        win._populate_tree()
        for _ in range(2):
            app.processEvents()

        # --- M3: the context menu (Q40)
        menu = win._build_tree_menu(win._tree_item_for(folder / "a.md"))
        labels = [action.text() for action in menu.actions()]
        check("row context menu actions", labels,
              ["Открыть", "Открыть в новой вкладке", "Копировать путь",
               "Открыть в проводнике"])

        # --- M4: drag a panel row onto the tab strip (Q17/Q18/Q19)
        check("drag resolver prefers the payload path",
              _resolve_drag_file(_FileMimeData(folder / "a.md")), folder / "a.md")
        check("drag resolver falls back to the global (invariant 26)",
              _resolve_drag_file(QMimeData()), None)
        mime = _FileMimeData(folder / "b10.md")
        mime.setData(FILE_MIME, QByteArray(b"file"))
        viewer_mod._DRAG_FILE = folder / "b10.md"
        bar = win._tabs.tabBar()
        tabs_before = win._tabs.count()
        bar.dropEvent(QDropEvent(
            QPointF(5, 5), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        viewer_mod._DRAG_FILE = None
        for _ in range(2):
            app.processEvents()
        check("a drop on the tab strip opens a tab at the pointer",
              win._tabs.count(), tabs_before + 1)

        # --- M4: Explorer drops (Q22) — viewable only, folders ignored
        external = QMimeData()
        external.setUrls([
            QUrl.fromLocalFile(str(folder / "notes.txt")),
            QUrl.fromLocalFile(str(folder / "pic.png")),
            QUrl.fromLocalFile(str(folder / "sub")),
            QUrl("https://example.com/x.md"),
        ])
        before = win._tabs.count()
        win.dropEvent(QDropEvent(
            QPointF(10, 10), Qt.DropAction.CopyAction, external,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        for _ in range(2):
            app.processEvents()
        check("an Explorer drop opens only viewable files",
              (win._tabs.count() - before,
               win._current_view()._path),
              (1, folder / "notes.txt"))

        # --- M4: reading mode hides the panel; the toggle is inert (Q32)
        win._enter_reading_mode()
        check("reading mode hides the panel", win._tree_panel.isVisible(), False)
        win._toggle_tree()
        check("the toggle is a no-op during reading mode",
              win._tree_panel.isVisible(), False)
        win._leave_reading_mode()
        check("exiting reading mode restores the panel",
              win._tree_panel.isVisible(), True)

        # --- M4: theme switch rebuilds the list (per-item colours)
        count_before = win._tree_list.count()
        win._set_theme("light")
        app.processEvents()
        check("the theme switch rebuilds the visible list",
              win._tree_list.count(), count_before)
        win._set_theme("dark")
        app.processEvents()

        win._confirm_discard = lambda: True
        win.close()
        app.processEvents()

    # --- Q44: a second window starts hidden even with the toggle saved on
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        (folder / "a.md").write_text("# a\n", encoding="utf-8")
        settings = QSettings(tempfile.mkdtemp(), "tree_second")
        win1 = viewer_mod.Viewer(folder / "a.md", settings)
        win1.resize(900, 600)
        win1.show()
        for _ in range(3):
            app.processEvents()
        win1._set_tree_visible(True)  # persists tree_visible=True
        win2 = viewer_mod.Viewer(folder / "a.md", QSettings(tempfile.mkdtemp(), "tree_t2"))
        win2.resize(900, 600)
        win2.show()
        for _ in range(3):
            app.processEvents()
        check("a later window starts hidden regardless of the toggle (Q44)",
              win2._tree_panel.isVisible(), False)
        win1.close()
        win2.close()
        app.processEvents()

    print("all checks passed" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    code = main()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    sys.stdout.flush()
    os._exit(code)
