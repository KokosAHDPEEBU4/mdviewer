@echo off
REM Build the app (if needed) and wrap dist\MDViewer into an Inno Setup installer.
REM   build-installer.bat
REM Output: dist\MDViewer-Setup-<version>.exe
REM Requires Inno Setup 6: https://jrsoftware.org/isinfo.php
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo No .venv; run build.bat first.
    exit /b 1
)

"%PY%" -c "from version import APP_VERSION; print(APP_VERSION)" > "%TEMP%\mdviewer_ver.txt"
set /p VER=<"%TEMP%\mdviewer_ver.txt"
del "%TEMP%\mdviewer_ver.txt" 2>nul
if "!VER!"=="" (
    echo Could not read APP_VERSION from version.py.
    exit /b 1
)

if not exist "dist\MDViewer\MDViewer.exe" (
    echo dist\MDViewer not found - running build.bat ...
    call build.bat || exit /b 1
)

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 not found. Install it from https://jrsoftware.org/isinfo.php
    exit /b 1
)

"%ISCC%" /DAppVersion=!VER! "installer\MDViewer.iss" || exit /b 1
echo.
echo Built: dist\MDViewer-Setup-!VER!.exe
endlocal
