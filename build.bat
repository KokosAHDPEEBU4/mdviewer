@echo off
REM Build MD Viewer into dist\MDViewer\ with a clean, reproducible environment.
REM   build.bat          full build (venv + deps + PyInstaller)
REM   build.bat --run    build, then install and set as the default .md handler
setlocal

cd /d "%~dp0"
set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

echo === 1/4 interpreter ===
if not exist "%PY%" (
    py -3.14 -m venv "%VENV%" 2>nul || py -3 -m venv "%VENV%" || python -m venv "%VENV%"
)
if not exist "%PY%" (
    echo Could not create the virtual environment.
    exit /b 1
)
"%PY%" -c "import sys; print('python', sys.version.split()[0], sys.executable)"

echo === 2/4 dependencies ===
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r requirements.txt pyinstaller

echo === 3/4 clean ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo === 4/4 pyinstaller ===
"%PY%" -m PyInstaller --noconfirm --clean MDViewer.spec
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
REM The work dir holds a bootloader-only MDViewer.exe that cannot run on its own
REM (no _internal next to it). Remove it so dist\ stays the single entry point.
REM Antivirus can still hold a handle for a moment, hence the retries.
for /L %%i in (1,1,5) do (
    if exist build rmdir /s /q build
    if not exist build goto :clean
    ping -n 2 127.0.0.1 >nul
)
echo WARNING: could not remove build\, delete it by hand.
:clean

echo Built: dist\MDViewer\MDViewer.exe
if /I "%1"=="--run" (
    echo.
    echo === installing + setting as default handler for .md ===
    "%PY%" main.py --install-default
)
endlocal