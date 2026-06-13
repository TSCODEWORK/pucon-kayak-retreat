#!/bin/bash
# start.sh — Start the Pucon Kayak Retreat app
# Run this every day to launch the app.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check the venv exists
if [ ! -f "venv/bin/activate" ]; then
    echo ""
    echo "ERROR: Virtual environment not found."
    echo "Please run setup.sh first:"
    echo "  ./setup.sh"
    echo ""
    exit 1
fi

source venv/bin/activate

echo ""
echo "================================================"
echo "  Pucon Kayak Retreat"
echo "================================================"
echo "  App is running at http://localhost:5000"
echo "  Press Ctrl+C to stop."
echo "================================================"
echo ""

# Open browser after a short delay
(sleep 1.5 && open "http://localhost:5000") &

python main.py
