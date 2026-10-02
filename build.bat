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
echo Built: dist\MDViewer\MDViewer.exe
if /I "%1"=="--run" (
    echo.
    echo === installing + setting as default handler for .md ===
    "%PY%" main.py --install-default
)
endlocal