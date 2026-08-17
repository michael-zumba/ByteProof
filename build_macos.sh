#!/bin/bash
# Build the ByteProof macOS installer for a target architecture.
#
# Usage:
#   ./build_macos.sh            # native architecture
#   ./build_macos.sh arm64      # Apple Silicon
#   ./build_macos.sh x86_64     # Intel (cross-compiled from Apple Silicon)
#
# The x86_64 build requires a dedicated x86_64 Python virtual environment:
#
#   arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m venv .venv_x86
#   source .venv_x86/bin/activate
#   pip install -r requirements.txt
#   deactivate

set -e

# Add common Homebrew path
if [ -d "/opt/homebrew/bin" ]; then
    export PATH="/opt/homebrew/bin:$PATH"
fi

APP_NAME="ByteProof"
THIS_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Target architecture ----------------------------------------------------

TARGET_ARCH="${1:-$(uname -m)}"
case "$TARGET_ARCH" in
    arm64|aarch64)
        TARGET_ARCH="arm64"
        DMG_NAME="ByteProof_Installer_AppleSilicon.dmg"
        DIST_DIR="$THIS_DIR/dist"
        WORK_DIR="$THIS_DIR/build"
        VENV_PYTHON="$THIS_DIR/venv/bin/python"
        VENV_PYINSTALLER="$THIS_DIR/venv/bin/pyinstaller"
        VOLNAME="$APP_NAME Installer"
        ;;
    x86_64|intel)
        TARGET_ARCH="x86_64"
        DMG_NAME="ByteProof_Installer_Intel.dmg"
        DIST_DIR="$THIS_DIR/dist_intel"
        WORK_DIR="$THIS_DIR/build_intel"
        VENV_PYTHON="$THIS_DIR/.venv_x86/bin/python3"
        VENV_PYINSTALLER="$THIS_DIR/.venv_x86/bin/pyinstaller"
        VOLNAME="$APP_NAME Installer (Intel)"
        ;;
    *)
        echo "Error: unknown architecture '$TARGET_ARCH'. Use arm64 or x86_64." >&2
        exit 1
        ;;
esac

echo "=== ByteProof Build ($TARGET_ARCH) ==="
echo ""

# --- Prerequisite checks ----------------------------------------------------

if [ ! -f "$VENV_PYTHON" ]; then
    if [ "$TARGET_ARCH" = "x86_64" ]; then
        echo "Error: x86_64 virtual environment not found at .venv_x86/"
        echo ""
        echo "One-time setup required. Run these commands:"
        echo "  arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m venv .venv_x86"
        echo "  source .venv_x86/bin/activate"
        echo "  pip install -r requirements.txt"
        echo "  deactivate"
        echo ""
        echo "Then re-run: ./build_macos.sh x86_64"
        exit 1
    fi
    if ! command -v python3 &> /dev/null; then
        echo "Error: python3 not found. Please install Python 3.11+."
        exit 1
    fi
    echo "Warning: no venv found. Building with system python3 may produce a large app."
    VENV_PYTHON="$(command -v python3)"
    PYTHON_VERSION=$("$VENV_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
else
    echo "Using project virtual environment (lightweight build): $VENV_PYTHON"
    PYTHON_VERSION=$("$VENV_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
fi
echo "Python version: $PYTHON_VERSION"

if [ "$TARGET_ARCH" = "x86_64" ]; then
    ARCH=$(arch -x86_64 "$VENV_PYTHON" -c "import platform; print(platform.machine())")
    if [ "$ARCH" != "x86_64" ]; then
        echo "Error: venv Python is not x86_64 (got $ARCH)."
        echo "Recreate .venv_x86 with the setup commands above."
        exit 1
    fi
    echo "Verified: x86_64 Python at $VENV_PYTHON"
fi

if [ ! -f "$VENV_PYINSTALLER" ]; then
    if [ "$TARGET_ARCH" = "x86_64" ] || ! command -v pyinstaller &> /dev/null; then
        echo ""
        echo "PyInstaller not found. Installing dependencies from requirements.txt..."
        pip3 install -r "$THIS_DIR/requirements.txt"
    fi
fi

if ! command -v create-dmg &> /dev/null; then
    echo ""
    echo "create-dmg not found. Install it with: brew install create-dmg"
    exit 1
fi

# --- Build ------------------------------------------------------------------

echo ""
echo "Cleaning previous builds..."
rm -rf "$WORK_DIR" "$DIST_DIR"
rm -f "$THIS_DIR/$DMG_NAME"

echo ""
echo "Building .app with PyInstaller ($TARGET_ARCH)..."
cd "$THIS_DIR"
if [ "$TARGET_ARCH" = "x86_64" ]; then
    export BYTEPROOF_TARGET_ARCH="x86_64"
else
    unset BYTEPROOF_TARGET_ARCH
fi
if [ "$TARGET_ARCH" = "x86_64" ]; then
    arch -x86_64 "$VENV_PYINSTALLER" "ByteProof.spec" \
        --noconfirm \
        --distpath "$DIST_DIR" \
        --workpath "$WORK_DIR"
else
    if [ -f "$VENV_PYINSTALLER" ]; then
        "$VENV_PYINSTALLER" "ByteProof.spec" --noconfirm
    else
        pyinstaller "ByteProof.spec" --noconfirm
    fi
fi

# Verify .app was created
if [ ! -d "$DIST_DIR/$APP_NAME.app" ]; then
    echo "Error: $DIST_DIR/$APP_NAME.app was not created. Build failed."
    exit 1
fi

echo ""
echo "Applying stable code signature (preserves Accessibility permissions)..."
"$THIS_DIR/tools/sign_byteproof.sh" "$DIST_DIR/$APP_NAME.app" || echo "Warning: stable signing skipped."

DEV_ID="${BYTEPROOF_DEV_ID:-}"
if [ -z "$DEV_ID" ]; then
    DEV_ID=$(security find-identity -v -p codesigning 2>/dev/null \
        | grep -m1 "Developer ID Application" \
        | sed -E 's/^[^"]*"([^"]+)".*/\1/')
fi
if [ -n "$DEV_ID" ]; then
    echo ""
    echo "Notarizing with Apple Developer ID: $DEV_ID"
    BYTEPROOF_DEV_ID="$DEV_ID" "$THIS_DIR/scripts/notarize.sh" "$DIST_DIR/$APP_NAME.app"
else
    echo ""
    echo "Notarization skipped (no Developer ID Application certificate found)."
    echo "Without notarization, macOS may show 'Apple could not verify ByteProof'."
fi

echo ""
echo "Creating .dmg installer..."
create-dmg \
  --volname "$VOLNAME" \
  --volicon "logo/logo.icns" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "$APP_NAME.app" 200 190 \
  --hide-extension "$APP_NAME.app" \
  --app-drop-link 400 185 \
  --skip-jenkins \
  "$DMG_NAME" \
  "$DIST_DIR/$APP_NAME.app"

if [ ! -f "$THIS_DIR/$DMG_NAME" ]; then
    echo "Error: create-dmg did not produce $DMG_NAME. Re-run the build (Finder scripting is sometimes flaky)." >&2
    exit 1
fi

if [ -n "$DEV_ID" ]; then
    echo ""
    echo "Signing installer with Apple Developer ID..."
    codesign --force --options runtime --timestamp --sign "$DEV_ID" "$THIS_DIR/$DMG_NAME"

    echo ""
    echo "Notarizing installer with Apple notary service..."
    xcrun notarytool submit "$THIS_DIR/$DMG_NAME" \
        --keychain-profile "${BYTEPROOF_NOTARY_PROFILE:-ByteProof-Notary}" \
        --keychain "${BYTEPROOF_NOTARY_KEYCHAIN:-$HOME/Library/Keychains/login.keychain-db}" \
        --wait

    echo ""
    echo "Stapling notarization ticket to installer..."
    xcrun stapler staple "$THIS_DIR/$DMG_NAME"
fi

echo ""
echo "Build complete! Installer: $DMG_NAME"
echo "Architecture: $TARGET_ARCH"
