#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Pucon Kayak Retreat — Mac app build script
# Run once from the project directory:  bash build.sh
# Output: dist/PuconKayakRetreat.dmg
# ─────────────────────────────────────────────────────────────────────────────
set -e

# ── Python — use the same interpreter that runs the app ──────────────────────
# The app's packages are installed into the CommandLineTools Python 3.9.
# If you've moved to a venv or a different Python, update this path.
PYTHON=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9

if [ ! -f "$PYTHON" ]; then
  echo "❌ Python 3.9 not found at $PYTHON"
  echo "   Install Xcode Command Line Tools:  xcode-select --install"
  exit 1
fi

PIP="$PYTHON -m pip"
PYINSTALLER="$PYTHON -m PyInstaller"
APP_NAME="PuconKayakRetreat"
DMG_NAME="${APP_NAME}.dmg"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Pucon Kayak Retreat — Mac App Builder"
echo " Python: $($PYTHON --version)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Install / upgrade Python dependencies ──────────────────────────────────
echo ""
echo "▸ Installing Python dependencies…"
$PIP install -q --upgrade \
  flask \
  python-dotenv \
  gspread \
  google-auth \
  google-auth-oauthlib \
  pywebview \
  pillow \
  pyinstaller

# ── 2. Verify client_secrets.json is present ──────────────────────────────────
if [ ! -f "client_secrets.json" ]; then
  echo ""
  echo "⚠️  client_secrets.json not found — Google OAuth will not work in the bundle."
  echo "   Download it from Google Cloud Console → Credentials and place it here."
  echo "   Continuing build without it…"
fi

# ── 3. Generate app icon ───────────────────────────────────────────────────────
echo ""
echo "▸ Generating icon…"
$PYTHON create_icon.py

# ── 4. Build the .app with PyInstaller ────────────────────────────────────────
echo ""
echo "▸ Building .app bundle (this takes 1–3 minutes)…"
$PYINSTALLER pucon_kayak.spec --noconfirm --clean

# ── 5. Sign the app (ad-hoc — no Apple Developer account required) ────────────
echo ""
echo "▸ Ad-hoc code-signing…"
if codesign --force --deep --sign - "dist/${APP_NAME}.app" 2>/dev/null; then
  echo "  ✓ Signed (ad-hoc). Users will need to right-click → Open on first launch."
else
  echo "  (codesign unavailable — app will still run, just needs 'Open Anyway' in Security settings)"
fi

# ── 6. Create DMG ─────────────────────────────────────────────────────────────
echo ""
echo "▸ Creating DMG…"
rm -f "dist/${DMG_NAME}"

STAGING="dist/_dmg_staging"
rm -rf "$STAGING"
mkdir "$STAGING"
cp -r "dist/${APP_NAME}.app" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

hdiutil create \
  -volname "Pucon Kayak Retreat" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  -imagekey zlib-level=9 \
  "dist/${DMG_NAME}"

rm -rf "$STAGING"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✓ Done!  →  dist/${DMG_NAME}"
echo ""
echo " To install:"
echo "   1. Open dist/${DMG_NAME}"
echo "   2. Drag Pucon Kayak Retreat → Applications"
echo "   3. First launch: right-click → Open  (bypasses Gatekeeper)"
echo "   4. The app opens in its own window — no browser needed"
echo "   5. Go to Settings to connect Google Sheets"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
