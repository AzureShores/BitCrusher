#!/bin/sh
# BitCrusher launcher (Linux/macOS). First run builds a local .venv and installs
# deps; later runs just launch. Needs ffmpeg/ffprobe on PATH (or the app fetches
# them). Run: ./bitcrusher.sh   (chmod +x bitcrusher.sh once)
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Python 3 not found. Install it from https://www.python.org/downloads/"
    echo "or your package manager (e.g. sudo apt install python3 python3-venv)."
    exit 1
fi

# A local venv avoids PEP 668 'externally-managed-environment' errors on modern
# distros and keeps deps out of the system Python.
if [ ! -f .deps_installed ]; then
    echo "First run: creating virtual environment and installing dependencies..."
    "$PY" -m venv .venv || {
        echo "venv creation failed. On Debian/Ubuntu: sudo apt install python3-venv"
        exit 1
    }
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q -r requirements.txt || {
        echo "Dependency install failed - check your internet connection."
        exit 1
    }
    : > .deps_installed
fi

exec .venv/bin/python BitCrusherV9.py "$@"
