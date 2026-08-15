#!/bin/bash
#
# Notarize ByteProof with Apple so Gatekeeper stops showing
# "Apple could not verify that ByteProof.app is free of malware."
#
# Prerequisites:
#   1. A paid Apple Developer account (https://developer.apple.com).
#   2. A "Developer ID Application" certificate installed in the login
#      keychain (created in Xcode > Settings > Accounts > Manage Certificates).
#   3. Notary credentials stored once:
#        xcrun notarytool store-credentials "ByteProof-Notary" \
#          --apple-id "you@example.com" \
#          --team-id "TEAMID" \
#          --password "app-specific-password"
#      (the password is an app-specific password from appleid.apple.com,
#       never your normal Apple ID password).
#
# Usage:
#   BYTEPROOF_DEV_ID="Developer ID Application: Your Name (TEAMID)" \
#     ./scripts/notarize.sh [path/to/ByteProof.app]

set -euo pipefail

THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$THIS_DIR")"
APP_PATH="${1:-$ROOT_DIR/dist/ByteProof.app}"
IDENTITY="${BYTEPROOF_DEV_ID:-}"
KEYCHAIN_PROFILE="${BYTEPROOF_NOTARY_PROFILE:-ByteProof-Notary}"

if [ -z "$IDENTITY" ]; then
    echo "Error: set BYTEPROOF_DEV_ID, e.g." >&2
    echo "  BYTEPROOF_DEV_ID=\"Developer ID Application: Your Name (TEAMID)\"" >&2
    exit 1
fi

if [ ! -d "$APP_PATH" ]; then
    echo "Error: $APP_PATH not found." >&2
    exit 1
fi

echo "Re-signing with Apple Developer ID..."
codesign --deep --force --options runtime --timestamp --sign "$IDENTITY" "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

echo "Packaging for notarization..."
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
ZIP_PATH="$WORK_DIR/ByteProof.zip"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

echo "Submitting to Apple notary service..."
xcrun notarytool submit "$ZIP_PATH" \
    --keychain-profile "$KEYCHAIN_PROFILE" \
    --wait

echo "Stapling the notarization ticket..."
xcrun stapler staple "$APP_PATH"
echo "Notarization complete."
