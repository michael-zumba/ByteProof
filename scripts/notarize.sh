#!/bin/bash
#
# Notarize ByteProof with Apple so Gatekeeper stops showing
# "Apple could not verify that ByteProof.app is free of malware."
#
# Prerequisites:
#   1. A paid Apple Developer account (https://developer.apple.com).
#   2. A "Developer ID Application" certificate installed in the login
#      keychain (created in Xcode > Settings > Accounts > Manage Certificates).
#   3. Notary credentials stored once (App Store Connect API key):
#        xcrun notarytool store-credentials "ByteProof-Notary" \
#          --key /path/to/AuthKey_XXXXXX.p8 \
#          --key-id "KEYID" \
#          --issuer "ISSUER-ID" \
#          --keychain "$HOME/Library/Keychains/login.keychain-db"
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
KEYCHAIN="${BYTEPROOF_NOTARY_KEYCHAIN:-$HOME/Library/Keychains/login.keychain-db}"

if [ -z "$IDENTITY" ]; then
    IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null \
        | grep -m1 "Developer ID Application" \
        | sed -E 's/^[^"]*"([^"]+)".*/\1/')
fi
if [ -z "$IDENTITY" ]; then
    echo "Error: no Developer ID Application certificate found in the login keychain." >&2
    echo "Create one in the Apple developer portal, install it, then re-run." >&2
    exit 1
fi

if [ ! -d "$APP_PATH" ]; then
    echo "Error: $APP_PATH not found." >&2
    exit 1
fi

echo "Re-signing with Apple Developer ID: $IDENTITY"
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
    --keychain "$KEYCHAIN" \
    --wait

echo "Stapling the notarization ticket..."
xcrun stapler staple "$APP_PATH"
echo "Notarization complete."
echo "Notarization complete."
