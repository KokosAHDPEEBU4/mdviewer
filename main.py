"""MD Viewer вЂ” a fast, self-contained markdown viewer for Windows.

Usage:
    MDViewer [file.md ...]      open files in tabs (one window)
    MDViewer --install-default  install + make the default handler for .md
    MDViewer --uninstall-default
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

import instance
import winassoc
from theme import APP_QSS
from version import APP_NAME, APP_VERSION
from viewer import MainWindow

ICON_PATH = Path(__file__).with_name("assets") / "icon.ico"

USAGE = f"""\
{APP_NAME} {APP_VERSION}
  MDViewer [файл ...]            открыть markdown-файлы вкладками в одном окне
  MDViewer --install-default     установить и назначить обработчиком .md
  MDViewer --no-prompt           ... без диалога «Открыть с помощью»
  MDViewer --uninstall-default   удалить регистрацию
  MDViewer --assoc-status        показать, что зарегистрировано в Windows
  MDViewer --restore-session     восстановить вкладки прошлой сессии
  MDViewer -h | -v
"""


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure") and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    prompt = "--no-prompt" not in argv
    force_restore = "--restore-session" in argv
    files = []
    for arg in argv:
        if arg in ("-h", "--help"):
            print(USAGE, end="")
            return 0
        if arg in ("-v", "--version"):
            print(f"{APP_NAME} {APP_VERSION}")
            return 0
        if arg == "--install-default":
            return report(winassoc.install(prompt))
        if arg == "--uninstall-default":
            return report(winassoc.uninstall())
        if arg == "--assoc-status":
            return report(winassoc.status())
        if arg in ("--no-prompt", "--restore-session"):
            continue
        files.append(Path(arg))

    app = create_app()
    settings = QSettings()

    # Single instance: a second launch (normally a .md double-click) hands its
    # paths to the running viewer as tabs and exits. `handle` and `watcher`
    # must outlive `app.exec()` below, hence they stay as locals here.
    handle, already_running = instance.acquire_singleton()
    if already_running:
        instance.write_request([path for path in files if path.is_file()])
        return 0

    window = MainWindow(settings)
    watcher = instance.RequestWatcher(window.open_external)
    paths = [path for path in files if path.is_file()]
    if paths:
        window._open_paths(paths)
        window.show()
        return app.exec()
    if files:
        return 0  # CLI paths were given, but none of them is a file
    # no paths on the command line: restore the previous session if asked (flag
    # or the Settings toggle), otherwise ask, and quit if nothing was picked
    if force_restore or settings.value("restore_session", False, type=bool):
        if window._restore_session():
            window.show()
            return app.exec()
    window._open_files_dialog()
    if window._tabs.count() == 0:
        return 0
    window.show()
    return app.exec()


def report(result: tuple[int, str]) -> int:
    """The frozen build is windowed, so results go to a dialog, not stdout."""
    code, message = result
    if sys.stdout is None:
        app = create_app()
        QMessageBox.information(None, APP_NAME, message)
        del app
    else:
        print(message)
    return code


def create_app() -> QApplication:
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("MDViewer")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyleSheet(APP_QSS)
    app.setQuitOnLastWindowClosed(True)  # closing the last tab closes the app
    return app


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))