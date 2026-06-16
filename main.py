"""
Entry point for the Pucon Kayak Retreat Mac app.
- Resolves the bundled resource path (PyInstaller sys._MEIPASS)
- Creates ~/Library/Application Support/PuconKayakRetreat/ for user data
- Starts Flask on a random free port in a background thread
- Opens a native macOS window via pywebview
"""

import sys
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path


# ── Bundle path (PyInstaller sets sys._MEIPASS when frozen) ──────────────────
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

# Tell app.py where templates/static live
os.environ["PKR_BASE_DIR"] = str(BASE_DIR)

# Tell db.py where to store rental.db (set before app.py is imported)
# P-9: single constant, mkdir called immediately.
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "PuconKayakRetreat"
APP_SUPPORT.mkdir(parents=True, exist_ok=True)
os.environ["PKR_DB_PATH"] = str(APP_SUPPORT)

ENV_PATH = APP_SUPPORT / ".env"
if not ENV_PATH.exists():
    # Write a starter .env so the user knows what to fill in
    ENV_PATH.write_text(
        "# Pucon Kayak Retreat — configuration\n"
        "# Edit this file, then restart the app.\n\n"
        "GOOGLE_SHEET_ID=your_google_sheet_id_here\n"
        "GOOGLE_CREDENTIALS_FILE=credentials.json\n"
        f"SECRET_KEY=pkr-{os.urandom(8).hex()}\n"
        "APP_PIN=1234\n"
    )

# credentials.json is expected in the same folder as .env
os.chdir(str(APP_SUPPORT))

# Load env from App Support directory (before importing app.py)
from dotenv import load_dotenv
load_dotenv(ENV_PATH, override=True)


# ── Find a free port ──────────────────────────────────────────────────────────
def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

PORT = _free_port()


# ── Start Flask in background thread ─────────────────────────────────────────
def _run_flask():
    from app import app
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)

_flask_thread = threading.Thread(target=_run_flask, daemon=True)
_flask_thread.start()

# Wait until Flask is actually responding (up to 15 seconds)
for _ in range(150):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login", timeout=1)
        break
    except Exception:
        time.sleep(0.1)


# ── Open native macOS window ──────────────────────────────────────────────────
import webview

window = webview.create_window(
    title="Pucon Kayak Retreat",
    url=f"http://127.0.0.1:{PORT}",
    width=1280,
    height=820,
    min_size=(900, 640),
    background_color="#0f3d4a",
    text_select=False,
)

webview.start(debug=False)
