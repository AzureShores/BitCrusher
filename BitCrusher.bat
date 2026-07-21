@echo off
setlocal
cd /d "%~dp0"

REM Detect Python without nesting %errorlevel% inside a block: %errorlevel%
REM expands at PARSE time there, so a nested check reads a stale value. Use
REM && on `where` (runtime) and `if not defined` guards instead.
set "PY_CMD="
where py >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo Python 3 not found. Install it from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked during setup.
    pause
    exit /b 1
)

if not exist "%~dp0.deps_installed" (
    echo Installing dependencies, first run only...
    %PY_CMD% -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed - check your internet connection and try again.
        pause
        exit /b 1
    )
    echo done > "%~dp0.deps_installed"
)

%PY_CMD% BitCrusherV9.py %*

if errorlevel 1 pause
endlocal
