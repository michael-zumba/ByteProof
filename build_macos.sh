#!/bin/bash

# Build script for ByteProof - macOS Apple Silicon (arm64)
# Usage: ./build_macos.sh

set -e

# Add common Homebrew path
if [ -d "/opt/homebrew/bin" ]; then
    export PATH="/opt/homebrew/bin:$PATH"
fi

APP_NAME="ByteProof"
DMG_NAME="ByteProof_Installer_AppleSilicon.dmg"
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== ByteProof Build (Apple Silicon) ==="
echo ""

# --- Prerequisite checks ---

if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found. Please install Python 3.11+."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PYTHON_VERSION"

if ! command -v pyinstaller &> /dev/null; then
    echo ""
    echo "PyInstaller not found. Installing dependencies from requirements.txt..."
    pip3 install -r "$THIS_DIR/requirements.txt"
fi

if ! command -v create-dmg &> /dev/null; then
    echo ""
    echo "create-dmg not found. Install it with: brew install create-dmg"
    exit 1
fi

# --- Build ---

echo ""
echo "Cleaning previous builds..."
rm -rf "$THIS_DIR/build" "$THIS_DIR/dist"
rm -f "$THIS_DIR/$DMG_NAME"

echo "Building .app with PyInstaller..."
cd "$THIS_DIR"
pyinstaller "ByteProof.spec" --noconfirm

# Verify .app was created
if [ ! -d "dist/$APP_NAME.app" ]; then
    echo "Error: dist/$APP_NAME.app was not created. Build failed."
    exit 1
fi

echo ""
echo "Applying stable code signature (preserves Accessibility permissions)..."
"$THIS_DIR/tools/sign_byteproof.sh" "dist/$APP_NAME.app" || echo "Warning: stable signing skipped."

echo ""
echo "Creating .dmg installer..."
create-dmg \
  --volname "$APP_NAME Installer" \
  --volicon "logo/logo.icns" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "$APP_NAME.app" 200 190 \
  --hide-extension "$APP_NAME.app" \
  --app-drop-link 400 185 \
  "$DMG_NAME" \
  "dist/$APP_NAME.app"

if [ ! -f "$THIS_DIR/$DMG_NAME" ]; then
    echo "Error: create-dmg did not produce $DMG_NAME. Re-run the build (Finder scripting is sometimes flaky)." >&2
    exit 1
fi

echo ""
echo "Build complete! Installer: $DMG_NAME"
echo "Architecture: $(uname -m)"
