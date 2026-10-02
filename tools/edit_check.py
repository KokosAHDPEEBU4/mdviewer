"""Drives the edit mode without a user: open -> edit -> convert -> save.

Run from the project root:
    .venv\\Scripts\\python tools\\edit_check.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QTextBrowser

import viewer as viewer_mod
from main import create_app
from theme import MEASURE, MIN_COLUMN, side_margin

SOURCE = "# Title\r\n\r\nHello.\r\n"
UPDATED = "# Title\r\n\r\nHello, world.\r\n"

failures = []


def check(label, got, want):
    ok = got == want
    failures.append(label) if not ok else None
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (want {want!r})"))


def main() -> int:
    app = create_app()

    # --- column policy, independent of any window
    check("narrow window keeps minimal insets", side_margin(1000), 20)
    check("column stops at MEASURE", side_margin(1700), 200)
    check("wide window grows the bars", side_margin(3440), 1072)
    check("column never exceeds MEASURE", 3440 - 2 * side_margin(3440) <= MEASURE, True)
    check("text column stays readable", 3440 - 2 * side_margin(3440) >= MIN_COLUMN, True)

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.md"
        path.write_bytes(SOURCE.encode("utf-8"))

        win = viewer_mod.Viewer(path, QSettings(tempfile.mkdtemp(), "edit_check"))
        win.show()

        # --- read mode
        check("preview has the original text", "Hello." in win._browser.toPlainText(), True)
        check("title carries the version", win.windowTitle(), "sample.md — MD Viewer 2.0.0")
        check("dark title bar requested once", win._dark_titled, True)

        # --- enter edit mode
        win._enter_edit()
        check("editor visible", win._editing(), True)
        check("editor holds the source", win._editor.toPlainText().splitlines()[2], "Hello.")
        check("window widened", win.width() > 1000, True)
        check("editor panel visible", win._panel.isVisible(), True)
        sizes = win._splitter.sizes()
        check("outline, preview and editor are three panes", len(sizes), 3 if win._toc.isVisible() else 2)
        check("preview gets the middle", sizes[-2] > 400, True)
        check("editor gets the right part", sizes[-1] > 400, True)
        win._apply_split()
        win._browser.resize(sizes[-2], win._browser.height())
        win._set_margins()
        # setViewportMargins shrinks the viewport itself, so its width *is* the column
        column = win._browser.viewport().width()
        check("preview column keeps a usable width", column > 500, True)
        check("insets follow the preview column, not the window", win._side, side_margin(win._browser.contentsRect().width()))
        check("save disabled while clean", win._save_btn.isEnabled(), False)
        check("convert disabled while clean", win._convert_btn.isEnabled(), False)

        # --- type something
        win._editor.setPlainText(UPDATED)
        check("title marks unsaved changes", win.windowTitle(), "sample.md • — MD Viewer 2.0.0")
        check("save enabled after edit", win._save_btn.isEnabled(), True)
        check("convert enabled after edit", win._convert_btn.isEnabled(), True)

        # --- convert: preview updates, disk untouched
        win._convert()
        check("preview re-rendered", "Hello, world." in win._browser.toPlainText(), True)
        check("convert disabled after convert", win._convert_btn.isEnabled(), False)
        check("still unsaved after convert", win.windowTitle(), "sample.md • — MD Viewer 2.0.0")
        check("disk untouched by convert", path.read_bytes(), SOURCE.encode("utf-8"))

        # --- save: file written, preview already current
        check("save reports success", win._save(), True)
        check("file written as CRLF", path.read_bytes(), UPDATED.encode("utf-8"))
        check("no BOM added to a plain utf-8 file", path.read_bytes()[:3], b"# T")
        check("title clean after save", win.windowTitle(), "sample.md — MD Viewer 2.0.0")
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
        check("anchors injected", win._browser.toHtml().count("<a name=\"md-h"), 41)
        win._sync_outline()
        check("offsets measured on demand", win._outline_ready, True)
        check("first section is current at the top", win._toc.currentRow(), 0)
        win._overlay.setVisible(False)
        win._show_overlay()
        check("overlay appears on demand", win._overlay.isVisible(), True)
        check("overlay carries the edit button", win._edit_btn.parent() is win._overlay, True)
        last = win._toc.item(win._toc.count() - 1)
        win._outline_clicked(last)
        for _ in range(3):
            app.processEvents()
        win._sync_outline()
        check("clicking scrolls the preview", win._browser.verticalScrollBar().value() > 0, True)
        check("current section follows the click", win._toc.currentRow(), win._toc.count() - 1)
        win._toggle_outline()
        check("outline can be hidden", win._toc.isVisible(), False)
        check("visibility persisted", win._settings.value("toc_visible", True, type=bool), False)
        win._toggle_outline()
        check("outline comes back", win._toc.isVisible(), True)
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