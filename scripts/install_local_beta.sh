#!/bin/bash
#
# Put a locally built ByteProof.app into /Applications the way the app's own
# updater does, so macOS keeps ByteProof's Accessibility permission.
#
# Usage:
#     ./scripts/install_local_beta.sh [path/to/ByteProof.app]
#
# Why this exists (owner report, 2026-10-08: "every update asks me to grant
# the permission again"): copying a build *over* the installed bundle
# (`ditto dist/ByteProof.app /Applications/ByteProof.app`) updates the files
# inside the existing bundle, and macOS treats the result as a different app -
# the Accessibility grant is dropped and the owner has to add ByteProof again.
# An atomic replacement - stage beside the destination, rename the old bundle
# away, rename the new one in - is what the DMG updater already does, and it
# keeps the grant (verified on this machine 2026-10-08: trusted=True after the
# swap, trusted=False after a ditto merge).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="${1:-$ROOT/dist/ByteProof.app}"
DEST="/Applications/ByteProof.app"
STAGING="$DEST.update-staging"
BACKUP="$DEST.update-backup"
APP_BINARY_PATTERN="ByteProof.app/Contents/MacOS/ByteProof"

if [ ! -d "$SOURCE" ]; then
    echo "Error: $SOURCE not found. Build it first (./build_macos.sh)." >&2
    exit 1
fi

echo "Installing $(defaults read "$SOURCE/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo '?') to $DEST"

# 1. Quit the running app; it refuses a normal quit while it lives in the menu
#    bar, so the process is asked to stop directly and given a moment.
if pgrep -f "$APP_BINARY_PATTERN" >/dev/null 2>&1; then
    echo "Stopping the running ByteProof..."
    pkill -f "$APP_BINARY_PATTERN" || true
    for _ in $(seq 1 40); do
        pgrep -f "$APP_BINARY_PATTERN" >/dev/null 2>&1 || break
        sleep 0.25
    done
    if pgrep -f "$APP_BINARY_PATTERN" >/dev/null 2>&1; then
        echo "Error: ByteProof is still running; quit it and try again." >&2
        exit 1
    fi
fi

# 2. Stage the new bundle beside the old one, then swap by rename.
echo "Staging the new bundle..."
/bin/rm -rf "$STAGING"
/usr/bin/ditto "$SOURCE" "$STAGING"

echo "Swapping bundles..."
/bin/rm -rf "$BACKUP"
if [ -e "$DEST" ]; then
    /bin/mv "$DEST" "$BACKUP"
fi
if ! /bin/mv "$STAGING" "$DEST"; then
    echo "Error: could not move the new bundle into place." >&2
    if [ -e "$BACKUP" ]; then
        /bin/mv "$BACKUP" "$DEST" 2>/dev/null || true
    fi
    exit 1
fi

# 3. Verify before the old bundle is dropped.
VERSION="$(defaults read "$DEST/Contents/Info.plist" CFBundleShortVersionString 2>/dev/null || echo '?')"
if ! /usr/bin/codesign --verify --deep --strict "$DEST"; then
    echo "Error: the new bundle does not verify; putting the old one back." >&2
    /bin/rm -rf "$DEST"
    [ -e "$BACKUP" ] && /bin/mv "$BACKUP" "$DEST"
    exit 1
fi
/bin/rm -rf "$BACKUP"

echo "Installed ByteProof $VERSION (signature verified)."
/usr/bin/open -a "$DEST"
echo "ByteProof is running. Its Accessibility permission was left in place."
