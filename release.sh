#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Pucon Kayak Retreat — Release script
# Usage:  bash release.sh "1.0.2" "What changed in this release"
# Requires: GITHUB_TOKEN env var, or token stored in git remote URL
# ─────────────────────────────────────────────────────────────────────────────
set -e

VERSION="${1}"
NOTES="${2:-Bug fixes and improvements}"
REPO="TSCODEWORK/pucon-kayak-retreat"
APP_NAME="PuconKayakRetreat"
DMG="dist/${APP_NAME}.dmg"

if [ -z "$VERSION" ]; then
  echo "Usage: bash release.sh <version> [\"release notes\"]"
  echo "Example: bash release.sh 1.0.2 \"Fix pricing bug\""
  exit 1
fi

# Extract token from git remote URL
GITHUB_TOKEN=$(git remote get-url origin 2>/dev/null | sed 's|.*://[^:]*:\([^@]*\)@.*|\1|')
if [ -z "$GITHUB_TOKEN" ]; then
  echo "❌ Could not find GitHub token in git remote URL."
  echo "   Set GITHUB_TOKEN env var or re-add origin with token in URL."
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Releasing v${VERSION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Update version in app.py ───────────────────────────────────────────────
echo ""
echo "▸ Updating APP_VERSION to ${VERSION}…"
sed -i '' "s/^APP_VERSION = .*/APP_VERSION = \"${VERSION}\"/" app.py

# ── 2. Update version in spec ─────────────────────────────────────────────────
sed -i '' "s/version=\"[0-9.]*\"/version=\"${VERSION}\"/g" pucon_kayak.spec
sed -i '' "s/\"CFBundleVersion\":.*\"[0-9.]*\"/\"CFBundleVersion\":           \"${VERSION}\"/" pucon_kayak.spec
sed -i '' "s/\"CFBundleShortVersionString\":.*\"[0-9.]*\"/\"CFBundleShortVersionString\":\"${VERSION}\"/" pucon_kayak.spec

# ── 3. Build the DMG ─────────────────────────────────────────────────────────
echo ""
echo "▸ Building DMG…"
bash build.sh

# ── 4. Update version.json ───────────────────────────────────────────────────
echo ""
echo "▸ Updating version.json…"
cat > version.json <<JSON
{
  "version": "${VERSION}",
  "download_url": "https://github.com/${REPO}/releases/download/v${VERSION}/${APP_NAME}.dmg",
  "release_notes": "${NOTES}"
}
JSON

# ── 5. Commit & tag ───────────────────────────────────────────────────────────
echo ""
echo "▸ Committing and tagging v${VERSION}…"
git add app.py pucon_kayak.spec version.json
git commit -m "Release v${VERSION} — ${NOTES}"
git tag "v${VERSION}"
git push origin master
git push origin "v${VERSION}"

# ── 6. Create GitHub Release ──────────────────────────────────────────────────
echo ""
echo "▸ Creating GitHub Release…"
RELEASE_RESPONSE=$(curl -s -X POST \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/${REPO}/releases" \
  -d "{
    \"tag_name\": \"v${VERSION}\",
    \"name\": \"v${VERSION}\",
    \"body\": \"${NOTES}\",
    \"draft\": false,
    \"prerelease\": false
  }")

UPLOAD_URL=$(echo "$RELEASE_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['upload_url'])" | sed 's/{?name,label}//')

if [ -z "$UPLOAD_URL" ]; then
  echo "❌ Failed to create release. Response:"
  echo "$RELEASE_RESPONSE"
  exit 1
fi

# ── 7. Upload DMG ─────────────────────────────────────────────────────────────
echo ""
echo "▸ Uploading ${DMG} ($(du -sh "$DMG" | cut -f1))…"
curl -s -X POST \
  -H "Authorization: token ${GITHUB_TOKEN}" \
  -H "Content-Type: application/octet-stream" \
  "${UPLOAD_URL}?name=${APP_NAME}.dmg" \
  --data-binary @"${DMG}" > /dev/null

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✓ Released v${VERSION}"
echo ""
echo " GitHub Release: https://github.com/${REPO}/releases/tag/v${VERSION}"
echo " DMG download:   https://github.com/${REPO}/releases/download/v${VERSION}/${APP_NAME}.dmg"
echo ""
echo " Users with the app will see the update banner on next launch."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
