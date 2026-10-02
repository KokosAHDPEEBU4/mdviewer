"""MD Viewer вЂ” a fast, self-contained markdown viewer for Windows.

Usage:
    MDViewer [file.md ...]      open files (one window each)
    MDViewer --install-default  install + make the default handler for .md
    MDViewer --uninstall-default
"""

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

import winassoc
from theme import APP_QSS
from version import APP_NAME, APP_VERSION
from viewer import Viewer

ICON_PATH = Path(__file__).with_name("assets") / "icon.ico"

USAGE = f"""\
{APP_NAME} {APP_VERSION}
  MDViewer [file ...]            open markdown files, one window each
  MDViewer --install-default     install and register as the .md handler
  MDViewer --no-prompt           ... without the "Open with" dialog
  MDViewer --uninstall-default   remove the registration
  MDViewer --assoc-status        show what Windows has registered
  MDViewer -h | -v
"""


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure") and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    prompt = "--no-prompt" not in argv
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
        if arg == "--no-prompt":
            continue
        files.append(Path(arg))

    app = create_app()
    paths = [path for path in files if path.is_file()]
    if not files:
        paths = ask_for_files(app, QSettings())
    for path in paths:
        Viewer(path).show()
    return app.exec() if paths else 0


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
    return app


def ask_for_files(app: QApplication, settings: QSettings) -> list[Path]:
    start = settings.value("last_dir", str(Path.home()))
    names, _ = QFileDialog.getOpenFileNames(
        app, APP_NAME, start, "Markdown (*.md *.markdown *.txt);;All files (*)"
    )
    settings.setValue("last_dir", start)
    return [Path(name) for name in names]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))