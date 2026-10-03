"""Single source of truth for identity/version, used by the app, the
PyInstaller spec (exe metadata) and the file-association installer."""

APP_NAME = "MD Viewer"
APP_VERSION = "2.4.1"
APP_PUBLISHER = "MD Viewer"
APP_DESCRIPTION = "Fast markdown viewer"

# Windows file-type association (ProgID), see winassoc.py
PROG_ID = "MDViewer.MarkdownFile"
EXTENSIONS = (".md",)