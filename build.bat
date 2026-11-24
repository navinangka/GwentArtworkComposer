@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 (
    echo Could not access the project directory.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    if exist ".venv" (
        echo The .venv directory exists but does not contain Python. Fix or remove it first.
        goto :error
    )
    py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))"
    if errorlevel 1 (
        echo Python 3.12 or newer and the Python launcher are required.
        goto :error
    )
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 12))"
if errorlevel 1 (
    echo The .venv environment must use Python 3.12 or newer.
    goto :error
)

".venv\Scripts\python.exe" -c "import PIL, PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Installing Pillow and PyInstaller...
    ".venv\Scripts\python.exe" -m pip install Pillow PyInstaller
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" source\build.py
if errorlevel 1 goto :error

echo Build complete: dist\GwentArtworkComposer.exe
popd
pause
exit /b 0

:error
echo Build failed.
popd
pause
exit /b 1
