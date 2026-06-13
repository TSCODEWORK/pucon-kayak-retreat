#!/bin/bash
# setup.sh — First-time setup for Pucon Kayak Retreat
# Run this once before using the app.

set -e

echo ""
echo "================================================"
echo "  Pucon Kayak Retreat — First-Time Setup"
echo "================================================"
echo ""

# ── Check Python 3.9+ ────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
        major=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON="$cmd"
            echo "✓ Found Python $major.$minor ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "ERROR: Python 3.9 or later is required."
    echo ""
    echo "Please install Python from:"
    echo "  https://www.python.org/downloads/"
    echo ""
    echo "Download the latest macOS installer, run it, then run this script again."
    exit 1
fi

# ── Create virtual environment ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    echo "✓ Virtual environment already exists (skipping creation)"
else
    echo "→ Creating virtual environment in venv/ ..."
    "$PYTHON" -m venv venv
    echo "✓ Virtual environment created"
fi

# ── Install requirements ─────────────────────────────────────────────────────
echo "→ Installing dependencies (this may take a minute) ..."
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -r requirements.txt --quiet
echo "✓ Dependencies installed"

# ── Create .env file if it doesn't exist ────────────────────────────────────
if [ -f ".env" ]; then
    echo "✓ .env file already exists (skipping)"
else
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(24))" 2>/dev/null || echo "change-me-to-a-random-string")
    cat > .env <<EOF
# Pucon Kayak Retreat — configuration
# You can change these settings here or in the app under Settings.

APP_PIN=1234
SECRET_KEY=$SECRET

# Optional: Google Sheets backup
# GOOGLE_SHEET_ID=your_google_sheet_id_here
# GOOGLE_CREDENTIALS_FILE=credentials.json
EOF
    echo "✓ Created .env with default settings (PIN: 1234)"
fi

echo ""
echo "================================================"
echo "  Setup complete!"
echo ""
echo "  To start the app, run:"
echo "    ./start.sh"
echo ""
echo "  Or double-click 'run.command' in Finder."
echo "================================================"
echo ""
