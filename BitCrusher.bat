@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 BitCrusherV9.py %*
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python BitCrusherV9.py %*
    ) else (
        echo Python 3 not found. Install it from https://www.python.org/downloads/
        echo and make sure "Add python.exe to PATH" is checked during setup.
        pause
        exit /b 1
    )
)

if errorlevel 1 pause
endlocal
