#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Pucon Kayak Retreat — Mac app build script
# Run once from the project directory:  bash build.sh
# Output: dist/PuconKayakRetreat.dmg
# ─────────────────────────────────────────────────────────────────────────────
set -e

PYTHON=/usr/bin/python3
PIP="$PYTHON -m pip"
PYINSTALLER="$PYTHON -m PyInstaller"
APP_NAME="PuconKayakRetreat"
DMG_NAME="${APP_NAME}.dmg"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Pucon Kayak Retreat — Mac App Builder"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Install Python dependencies ────────────────────────────────────────────
echo ""
echo "▸ Installing Python dependencies…"
$PIP install -q \
  flask \
  python-dotenv \
  gspread \
  google-auth \
  pywebview \
  pillow \
  pyinstaller

# ── 2. Generate app icon ───────────────────────────────────────────────────────
echo ""
echo "▸ Generating icon…"
$PYTHON create_icon.py

# ── 3. Build the .app with PyInstaller ────────────────────────────────────────
echo ""
echo "▸ Building .app bundle (this takes 1–3 minutes)…"
$PYINSTALLER pucon_kayak.spec --noconfirm --clean

# ── 4. Sign the app (ad-hoc, no Apple Developer account needed) ───────────────
echo ""
echo "▸ Ad-hoc signing (allows running on this Mac without Gatekeeper errors)…"
codesign --force --deep --sign - "dist/${APP_NAME}.app" 2>/dev/null || \
  echo "  (codesign skipped — app will still run, may need 'Open Anyway' in Security settings)"

# ── 5. Create DMG ─────────────────────────────────────────────────────────────
echo ""
echo "▸ Creating DMG…"
rm -f "dist/${DMG_NAME}"

# Create a temporary folder with the app and an Applications symlink
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
echo " ✓ Done!  dist/${DMG_NAME}"
echo ""
echo " To install:"
echo "   1. Open dist/${DMG_NAME}"
echo "   2. Drag Pucon Kayak Retreat → Applications"
echo "   3. First launch: right-click the app → Open (bypasses Gatekeeper)"
echo "   4. Place credentials.json in:"
echo "      ~/Library/Application Support/PuconKayakRetreat/"
echo "   5. Edit the .env file in that same folder with your Sheet ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
