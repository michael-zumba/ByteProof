#!/bin/bash

# Build script for ByteProof - macOS Intel (x86_64)
#
# IMPORTANT: This script cross-compiles from Apple Silicon to Intel.
# It requires a dedicated x86_64 Python virtual environment.
#
# One-time setup (run once):
#   arch -x86_64 /usr/bin/python3 -m venv .venv_x86
#   source .venv_x86/bin/activate
#   pip install -r requirements.txt
#   deactivate
#
# Then run: ./build_macos_intel.sh

set -e

# Add common Homebrew path
if [ -d "/opt/homebrew/bin" ]; then
    export PATH="/opt/homebrew/bin:$PATH"
fi

APP_NAME="ByteProof"
DMG_NAME="ByteProof_Installer_Intel.dmg"
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$THIS_DIR/.venv_x86/bin/python3"
VENV_PYINSTALLER="$THIS_DIR/.venv_x86/bin/pyinstaller"

echo "=== ByteProof Build (Intel x86_64) ==="
echo ""

# --- Prerequisite checks ---

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: x86_64 virtual environment not found at .venv_x86/"
    echo ""
    echo "One-time setup required. Run these commands:"
    echo "  arch -x86_64 /usr/bin/python3 -m venv .venv_x86"
    echo "  source .venv_x86/bin/activate"
    echo "  pip install -r requirements.txt"
    echo "  deactivate"
    echo ""
    echo "Then re-run: ./build_macos_intel.sh"
    exit 1
fi

# Verify architecture
ARCH=$(arch -x86_64 "$VENV_PYTHON" -c "import platform; print(platform.machine())")
if [ "$ARCH" != "x86_64" ]; then
    echo "Error: venv Python is not x86_64 (got $ARCH)."
    echo "Recreate .venv_x86 with: arch -x86_64 /usr/bin/python3 -m venv .venv_x86"
    exit 1
fi
echo "Verified: x86_64 Python at $VENV_PYTHON"

if ! command -v create-dmg &> /dev/null; then
    echo ""
    echo "create-dmg not found. Install it with: brew install create-dmg"
    exit 1
fi

# --- Build ---

echo ""
echo "Cleaning previous builds..."
rm -rf "$THIS_DIR/build_intel" "$THIS_DIR/dist_intel"
rm -f "$THIS_DIR/$DMG_NAME"

echo "Building .app with PyInstaller (Intel x86_64)..."
cd "$THIS_DIR"
arch -x86_64 "$VENV_PYINSTALLER" "ByteProof_intel.spec" \
    --noconfirm \
    --distpath dist_intel \
    --workpath build_intel

if [ ! -d "dist_intel/$APP_NAME.app" ]; then
    echo "Error: dist_intel/$APP_NAME.app was not created. Build failed."
    exit 1
fi

echo ""
echo "Applying stable code signature (preserves Accessibility permissions)..."
"$THIS_DIR/tools/sign_byteproof.sh" "dist_intel/$APP_NAME.app" || echo "Warning: stable signing skipped."

if [ -n "${BYTEPROOF_DEV_ID:-}" ]; then
    echo ""
    echo "Notarizing with Apple Developer ID..."
    "$THIS_DIR/scripts/notarize.sh" "dist_intel/$APP_NAME.app"
else
    echo ""
    echo "Notarization skipped (set BYTEPROOF_DEV_ID to notarize)."
    echo "Without notarization, macOS may show 'Apple could not verify ByteProof'."
fi

echo ""
echo "Creating .dmg installer..."
create-dmg \
  --volname "$APP_NAME Installer (Intel)" \
  --volicon "logo/logo.icns" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "$APP_NAME.app" 200 190 \
  --hide-extension "$APP_NAME.app" \
  --app-drop-link 400 185 \
  "$DMG_NAME" \
  "dist_intel/$APP_NAME.app"

if [ ! -f "$THIS_DIR/$DMG_NAME" ]; then
    echo "Error: create-dmg did not produce $DMG_NAME. Re-run the build (Finder scripting is sometimes flaky)." >&2
    exit 1
fi

echo ""
echo "Build complete! Installer: $DMG_NAME"
echo "Architecture: x86_64"
