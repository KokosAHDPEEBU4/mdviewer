"""Single source of truth for identity/version, used by the app, the
PyInstaller spec (exe metadata) and the file-association installer."""

APP_NAME = "MD Viewer"
APP_VERSION = "2.11.0"
APP_PUBLISHER = "MD Viewer"
APP_DESCRIPTION = "Fast markdown and JSON viewer"

# Windows file-type association (ProgID), see winassoc.py
PROG_ID = "MDViewer.MarkdownFile"
EXTENSIONS = (".md", ".json")