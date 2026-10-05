"""MD Viewer — a fast, self-contained markdown viewer for Windows.

Usage:
    MDViewer [file.md ...]      open files in tabs (one window)
    MDViewer --install-default  install + make the default handler for .md
    MDViewer --uninstall-default
"""

import sys
from pathlib import Path

import instance
from version import APP_NAME, APP_VERSION

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
            import winassoc
            return report(winassoc.install(prompt))
        if arg == "--uninstall-default":
            import winassoc
            return report(winassoc.uninstall())
        if arg == "--assoc-status":
            import winassoc
            return report(winassoc.status())
        if arg in ("--no-prompt", "--restore-session"):
            continue
        files.append(Path(arg))

    # Fast path for a second launch — usually a .md double-click. It must stay
    # free of Qt: the mutex check and the file hand-off are stdlib only, so the
    # running window picks the file up while this process is already gone.
    handle, already_running = instance.acquire_singleton()
    if already_running:
        instance.write_request([path for path in files if path.is_file()])
        return 0

    return _run_owner(handle, files, force_restore)


def _run_owner(handle, files, force_restore: bool) -> int:
    """The owning process: now it is worth importing Qt and building the window."""
    from PyQt6.QtCore import QSettings

    from viewer import MainWindow

    app = create_app()
    settings = QSettings()
    window = MainWindow(settings)
    # `handle` keeps the singleton mutex alive and `watcher` polls the queue;
    # both must outlive `app.exec()` below.
    watcher = instance.make_watcher(window.open_external)

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
        from PyQt6.QtWidgets import QMessageBox

        app = create_app()
        QMessageBox.information(None, APP_NAME, message)
        del app
    else:
        print(message)
    return code


def create_app():
    """Build the QApplication; Qt and the theme are imported here, not at start."""
    from PyQt6.QtCore import QSettings
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from theme import DEFAULT_THEME, app_qss

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("MDViewer")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    # apply the saved theme before any window exists, so the first frame is
    # already correct (no dark flash for a light-theme user)
    theme_name = QSettings().value("theme", DEFAULT_THEME, type=str)
    app.setStyleSheet(app_qss(theme_name))
    app.setQuitOnLastWindowClosed(True)  # closing the last tab closes the app
    return app


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
