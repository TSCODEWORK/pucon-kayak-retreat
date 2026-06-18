#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Pucon Kayak Retreat — Mac app build script
# Produces a universal2 (Intel + Apple Silicon) DMG.
# Run once from the project directory:  bash build.sh
# Output: dist/PuconKayakRetreat.dmg
# ─────────────────────────────────────────────────────────────────────────────
set -e

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
X86_PKGS=/tmp/pkr_x86_pkgs

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Pucon Kayak Retreat — Mac App Builder (universal2)"
echo " Python: $($PYTHON --version)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1a. Install arm64 dependencies ────────────────────────────────────────────
echo ""
echo "▸ Installing arm64 Python dependencies…"
$PIP install -q --upgrade \
  flask \
  python-dotenv \
  gspread \
  google-auth \
  google-auth-oauthlib \
  pywebview \
  pillow \
  pyinstaller \
  watchdog \
  reportlab \
  requests

# ── 1b. Install x86_64 dependencies (via Rosetta) ─────────────────────────────
echo ""
echo "▸ Installing x86_64 Python dependencies (via Rosetta)…"
rm -rf "$X86_PKGS"
arch -x86_64 "$PYTHON" -m pip install -q --upgrade \
  --target "$X86_PKGS" \
  flask \
  python-dotenv \
  gspread \
  google-auth \
  google-auth-oauthlib \
  pywebview \
  pillow \
  pyinstaller \
  watchdog \
  reportlab \
  requests \
  cffi \
  cryptography \
  markupsafe

# ── 2. Verify client_secrets.json ─────────────────────────────────────────────
if [ ! -f "client_secrets.json" ]; then
  echo ""
  echo "⚠️  client_secrets.json not found — Google OAuth will not work in the bundle."
  echo "   Continuing build without it…"
fi

# ── 3. Generate app icon ───────────────────────────────────────────────────────
echo ""
echo "▸ Generating icon…"
$PYTHON create_icon.py

# ── 4a. Build arm64 bundle ────────────────────────────────────────────────────
echo ""
echo "▸ Building arm64 bundle…"
$PYINSTALLER pucon_kayak.spec --noconfirm --clean
mv "dist/${APP_NAME}.app" "dist/${APP_NAME}_arm64.app"
rm -rf "dist/${APP_NAME}" build/pucon_kayak

# ── 4b. Build x86_64 bundle (via Rosetta + x86_64 packages) ──────────────────
echo ""
echo "▸ Building x86_64 bundle (via Rosetta)…"
PYTHONPATH="$X86_PKGS" arch -x86_64 "$PYTHON" -m PyInstaller \
  pucon_kayak.spec --noconfirm --clean
mv "dist/${APP_NAME}.app" "dist/${APP_NAME}_x86.app"
rm -rf "dist/${APP_NAME}" build/pucon_kayak

# ── 4c. Merge into universal2 .app ────────────────────────────────────────────
echo ""
echo "▸ Merging arm64 + x86_64 into universal2 bundle…"
ARM_APP="dist/${APP_NAME}_arm64.app"
X86_APP="dist/${APP_NAME}_x86.app"
UNI_APP="dist/${APP_NAME}.app"

rm -rf "$UNI_APP"
cp -r "$ARM_APP" "$UNI_APP"

merged=0
skipped=0
while IFS= read -r -d '' arm_file; do
  rel="${arm_file#${ARM_APP}/}"
  x86_file="${X86_APP}/${rel}"
  uni_file="${UNI_APP}/${rel}"
  if [ -f "$x86_file" ]; then
    arm_type=$(file -b "$arm_file")
    if echo "$arm_type" | grep -q "Mach-O"; then
      if lipo -create "$arm_file" "$x86_file" -output "$uni_file" 2>/dev/null; then
        merged=$((merged + 1))
      fi
    fi
  fi
done < <(find "$ARM_APP" -type f -print0)

echo "  ✓ Lipo'd $merged binaries into universal2"
rm -rf "$ARM_APP" "$X86_APP"

# ── 5. Verify universal2 ──────────────────────────────────────────────────────
echo ""
echo "▸ Verifying universal2 executable…"
EXEC_PATH="$UNI_APP/Contents/MacOS/${APP_NAME}"
if file "$EXEC_PATH" | grep -q "universal binary"; then
  echo "  ✓ $(file -b "$EXEC_PATH")"
else
  echo "  ⚠️  Main executable is not universal: $(file -b "$EXEC_PATH")"
fi

# ── 6. Sign the app (ad-hoc) ──────────────────────────────────────────────────
echo ""
echo "▸ Ad-hoc code-signing…"
if codesign --force --deep --sign - "$UNI_APP" 2>/dev/null; then
  echo "  ✓ Signed (ad-hoc). Users will need to right-click → Open on first launch."
else
  echo "  (codesign unavailable)"
fi

# ── 7. Create DMG ─────────────────────────────────────────────────────────────
echo ""
echo "▸ Creating DMG…"
rm -f "dist/${DMG_NAME}"

STAGING="dist/_dmg_staging"
rm -rf "$STAGING"
mkdir "$STAGING"
cp -r "$UNI_APP" "$STAGING/"
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
echo " ✓ Done!  →  dist/${DMG_NAME}  (universal2: Intel + Apple Silicon)"
echo ""
echo " To install:"
echo "   1. Open dist/${DMG_NAME}"
echo "   2. Drag Pucon Kayak Retreat → Applications"
echo "   3. First launch: right-click → Open  (bypasses Gatekeeper)"
echo "   4. The app opens in its own window — no browser needed"
echo "   5. Go to Settings to connect Google Sheets"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
