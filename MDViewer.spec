# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds dist/MDViewer/MDViewer.exe (+ _internal/).

Kept deliberately slim: the app only needs QtCore/QtGui/QtWidgets/QtSvg, so
network, pdf, web-engine, translations and the 20 MB software-OpenGL fallback
are dropped from the bundle.  One-dir instead of one-file: a cold start is
~0.8-1.0 s instead of a couple of seconds, which is the whole point of a viewer.
Qt is imported lazily by main.py, so a second launch (a .md double-click while
the viewer is already open) forwards the path without loading Qt at all (~0.1 s).
"""

import sys
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PROJECT = Path(SPECPATH)
sys.path.insert(0, str(PROJECT))
from version import APP_DESCRIPTION, APP_NAME, APP_VERSION, APP_PUBLISHER  # noqa: E402

# modules the app never touches (Qt deps come in via QtCore/QtGui/QtWidgets)
EXCLUDES = [
    "PyQt6.QtNetwork",
    "PyQt6.QtPdf",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQuick",
    "PyQt6.QtQml",
    "PyQt6.Qt3DCore",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtSql",
    "PyQt6.QtTest",
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets",
]

# payload that the hooks add but this app does not need
DROP_BINARIES = (
    "opengl32sw.dll",  # 20 MB software OpenGL fallback, unused by raster widgets
    "qpdf.dll",
    "Qt6Pdf.dll",
    "Qt6Network.dll",
    "libcrypto-3.dll",  # OpenSSL, only reachable through the TLS plugins below
    "libssl-3.dll",
    "qopensslbackend.dll",
    "qcertonlybackend.dll",
)
DROP_DATA_DIRS = ("translations",)

version_tuple = tuple(int(part) for part in APP_VERSION.split("."))
version_tuple += (0,) * (4 - len(version_tuple))
version_strings = {
    "CompanyName": APP_PUBLISHER,
    "FileDescription": APP_DESCRIPTION,
    "FileVersion": APP_VERSION,
    "InternalName": "MDViewer",
    "OriginalFilename": "MDViewer.exe",
    "ProductName": APP_NAME,
    "ProductVersion": APP_VERSION,
}

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version_tuple,
        prodvers=version_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",  # en-US, Unicode
                    [StringStruct(key, value) for key, value in version_strings.items()],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 0x04B0])]),
    ],
)


def _kept(path: str, names: tuple[str, ...]) -> bool:
    parts = Path(path).parts
    return not any(name in parts for name in names) and parts[-1] not in names


datas = [
    ("assets/icon.ico", "assets"),
    # tab close-button images (theme.app_qss references them by absolute path)
    ("assets/close_tab_dark.png", "assets"),
    ("assets/close_tab_dark_hover.png", "assets"),
    ("assets/close_tab_light.png", "assets"),
    ("assets/close_tab_light_hover.png", "assets"),
]

a = Analysis(
    [str(PROJECT / "main.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    # QPrinter (tab-menu "Экспорт -> PDF") lives in QtPrintSupport; list it
    # explicitly so the analyser cannot miss it when the import is only reached
    # through a slot. Its Qt6PrintSupport.dll and the Windows print plugin are
    # kept (not in DROP_BINARIES), which is exactly what PDF output needs.
    hiddenimports=["PyQt6.QtPrintSupport"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

a.binaries = [b for b in a.binaries if _kept(b[0], DROP_BINARIES)]
a.datas = [d for d in a.datas if _kept(d[0], DROP_DATA_DIRS)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MDViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(PROJECT / "assets" / "icon.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MDViewer",
)