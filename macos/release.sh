#!/bin/zsh
# Publishes a new version: ./macos/release.sh 1.0.1 "What changed"
# Builds and checks the app, signs the disk image, commits and tags the version, and creates the GitHub release
# that installed copies find through Check for Updates. Needs the GitHub CLI, signed in (gh auth login).
set -euo pipefail

ROOT="${0:a:h:h}"
PLIST="$ROOT/macos/Info.plist"
KEY="${LIVECAP_SIGNING_KEY:-$HOME/.livecap/update-signing-key}"
DMG="$ROOT/dist/LiveCap.dmg"
VERSION="${1:-}"
NOTES="${2:-}"

stop() { echo "$1" >&2; exit 1; }

[[ "$VERSION" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' && -n "$NOTES" ]] || stop 'Usage: ./macos/release.sh 1.0.1 "What changed"'
cd "$ROOT"
command -v gh >/dev/null || stop "The GitHub CLI isn't installed. See the README."
gh auth status >/dev/null 2>&1 || stop "Sign in first: gh auth login"
[[ -f "$KEY" ]] || stop "The signing key isn't at $KEY. Without that key, installed copies refuse new versions."
[[ "$(git branch --show-current)" == "main" ]] || stop "Switch to the main branch first."
git fetch --quiet origin
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || stop "This copy isn't level with GitHub. Push or pull first."
[[ -z "$(git status --porcelain)" ]] || stop "Commit your changes first, so the release matches the repository."
git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null && stop "v$VERSION already exists."

PUBLISHED="$(gh release view --json tagName --jq .tagName 2>/dev/null || true)"
newest="$(printf '%s\n%s\n' "$VERSION" "${PUBLISHED#v}" | sort -V | tail -1)"
[[ -z "$PUBLISHED" || ( "$newest" == "$VERSION" && "$VERSION" != "${PUBLISHED#v}" ) ]] || stop "v$VERSION isn't newer than $PUBLISHED."

BUILT_KEY="$(plutil -extract LiveCapUpdateKey raw "$PLIST")"
SIGNER="$(mktemp -t release_key)"
trap 'rm -f "$SIGNER"; [[ -n "${SERVER:-}" ]] && kill "$SERVER" 2>/dev/null; rm -rf "${HOME_CHECK:-}"; true' EXIT
swiftc -O "$ROOT/macos/release_key.swift" -o "$SIGNER"
[[ "$("$SIGNER" public "$KEY")" == "$BUILT_KEY" ]] || stop "The signing key doesn't match LiveCapUpdateKey in Info.plist."

plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$(( $(plutil -extract CFBundleVersion raw "$PLIST") + 1 ))" "$PLIST"
restore() { git checkout -- "$PLIST"; }

echo "Building $VERSION..."
env -u HF_HUB_OFFLINE zsh "$ROOT/macos/build.sh" || { restore; stop "The build failed, so nothing was published."; }

# Installed copies can't roll back, so a release that doesn't start must not get out.
echo "Checking that the built app starts..."
RES="$ROOT/build/LiveCap.app/Contents/Resources"
HOME_CHECK="$(mktemp -d -t livecap-check)"
(cd "$RES" && LIVECAP_DATA="$HOME_CHECK" LIVECAP_ADMIN_PASSWORD=check LIVECAP_HTTP_PORT=18765 LIVECAP_HTTPS_PORT=18766 LIVECAP_RTMP_PORT=18767 LIVECAP_SRT_PORT=18768 \
  LIVECAP_RECOGNIZER=parakeet LIVECAP_PARAKEET_DIR="$HOME/Library/Application Support/LiveCap/data/parakeet-tdt-0.6b-v3" \
  LIVECAP_SUPERTONIC_DIR="$RES/supertonic-3" LIVECAP_FFMPEG="$RES/bin/ffmpeg" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RES" \
  "$RES/python/bin/python3" -m app.main >"$HOME_CHECK/log" 2>&1) &
SERVER=$!
started=""
for attempt in {1..60}; do
  if curl -fsS -m 2 http://127.0.0.1:18765/ 2>/dev/null | grep -q 'id="captions"'; then started=yes; break; fi
  sleep 1
done
kill "$SERVER" 2>/dev/null || true
wait "$SERVER" 2>/dev/null || true
pkill -9 -f "rtmp://0.0.0.0:18767" 2>/dev/null || true
pkill -9 -f "srt://0.0.0.0:18768" 2>/dev/null || true
SERVER=""
[[ -n "$started" ]] || { cat "$HOME_CHECK/log" >&2; restore; stop "The built app didn't start, so nothing was published."; }
codesign --verify --strict "$ROOT/build/LiveCap.app" || { restore; stop "The built app's signature is invalid."; }
hdiutil verify "$DMG" >/dev/null 2>&1 || { restore; stop "The disk image is damaged."; }

SIGNATURE="$DMG.sig"
"$SIGNER" sign "$KEY" "$DMG" > "$SIGNATURE"

git add "$PLIST"
git commit -q -m "Release v$VERSION"
git tag "v$VERSION"
git push -q origin main "v$VERSION"
gh release create "v$VERSION" "$DMG" "$SIGNATURE" --title "LiveCap $VERSION" --notes "$NOTES" --latest
rm -f "$SIGNATURE"
echo "Published LiveCap $VERSION. Installed copies can now find it with Check for Updates."
