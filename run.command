#!/bin/bash
# run.command — Double-click this file in Finder to launch the app.
# macOS may ask for permission the first time — click OK.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f "venv/bin/activate" ]; then
    echo ""
    echo "First-time setup needed. Running setup..."
    echo ""
    bash setup.sh
fi

bash start.sh
