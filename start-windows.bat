@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
    py -3 start.py %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3.11+ is required. Install Python from https://www.python.org/downloads/
        echo Enable "Add Python to PATH" during installation.
    ) else (
        python start.py %*
    )
)
pause
