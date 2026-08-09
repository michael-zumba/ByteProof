# How to Build and Distribute "ByteProof" App

This guide explains how to build the application for both macOS (Apple Silicon
and Intel) and Windows, and how to distribute it via GitHub Releases.

## Part 1: Building the Application

### 1. Build for macOS (Apple Silicon / arm64)

**Prerequisites:** Mac with Apple Silicon, Python 3.11+, `create-dmg` installed
(`brew install create-dmg`).

1. Open Terminal in the project folder.
2. Run the build script:
   ```bash
   ./build_macos.sh
   ```
3. **Output:** `ByteProof_Installer_AppleSilicon.dmg` in the project root.

### 2. Build for macOS (Intel / x86_64) — Cross-compile from Apple Silicon

**Prerequisites:** Mac with Apple Silicon (needs Rosetta 2 and an x86_64 Python venv).

**One-time setup** (run once):

```bash
# Use a universal2 Python 3.11+ (e.g. python.org installer); the system
# /usr/bin/python3 on macOS is 3.9 and cannot run this codebase.
arch -x86_64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m venv .venv_x86
source .venv_x86/bin/activate
pip install -r requirements.txt
deactivate
```

Then build:

```bash
./build_macos_intel.sh
```

**Output:** `ByteProof_Installer_Intel.dmg` in the project root.

### 3. Build for Windows

**Prerequisites:** Windows computer with Python 3.11+.

1. **Transfer Files:** Copy the entire project folder to the Windows machine.
2. **Icon:** `logo/logo.ico` is already included in the project.
3. **Run Build Script:**
   - Double-click `build_windows.bat`.
   - The script installs dependencies, builds the app, and creates
     `ByteProof_Windows.zip` automatically.
4. **Manual alternative:**
   ```bat
   pip install -r requirements.txt
   pyinstaller --noconfirm ByteProof_win.spec
   powershell -NoProfile -Command "Compress-Archive -Path 'dist\ByteProof\*' -DestinationPath 'ByteProof_Windows.zip' -Force"
   ```

---

## Part 2: Distributing via GitHub Releases

### Step 1: Push Code to GitHub

```bash
git add .
git commit -m "Release version 1.0"
git push origin main
```

### Step 2: Create a Release

1. Go to your repository's **Releases** page.
2. Click **Draft a new release**.
3. **Tag:** e.g., `v1.0` (create new tag on publish).
4. **Title:** e.g., "ByteProof v1.0".

### Step 3: Upload Files

Upload the three distribution files:
- **macOS Apple Silicon:** `ByteProof_Installer_AppleSilicon.dmg`
- **macOS Intel:** `ByteProof_Installer_Intel.dmg`
- **Windows:** `ByteProof_Windows.zip`

### Step 4: Publish

Click **Publish release**.

---

## Part 3: Linking from Your Website

**Latest release always:**
`https://github.com/michael-zumba/ByteProof/releases/latest`

**Direct links (replace tag and repo):**
- **Mac (Apple Silicon):** `https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Installer_AppleSilicon.dmg`
- **Mac (Intel):** `https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Installer_Intel.dmg`
- **Windows:** `https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Windows.zip`

---

## In-App Updates

ByteProof checks `https://www.bytemind.co.nz/byteproof-version.json` (defined in
`src/app_version.py`) shortly after launch. Host a JSON file at that URL shaped
like this (a copy is included in the repo as `byteproof-version.json.example`):

```json
{
  "version": "1.0.1",
  "release_date": "2026-08-10",
  "release_notes": "Bug fixes and Windows improvements.",
  "macos_apple_silicon_url": "https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Installer_AppleSilicon.dmg",
  "macos_intel_url": "https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Installer_Intel.dmg",
  "windows_url": "https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Windows.zip"
}
```

Until that URL is live, the app silently skips the update check.

When a user clicks **Remind Me Later**, ByteProof remembers that version and
won't ask again until a newer version appears. Users can always choose
**Check for Updates…** from the tray icon or the Application menu for an
immediate manual check (which also shows a "You're up to date" confirmation).
The download shows progress in the status bar, then opens the installer
(DMG on macOS, installer/zip on Windows).

---

## Notes for End Users

### Accessibility Permissions (macOS)

The app uses global hotkeys and AppleScript to communicate with Microsoft Word.
On first launch, users may see a prompt to grant Accessibility and/or Automation
permissions. If hotkeys do not work:

1. Open **System Settings > Privacy & Security > Accessibility**.
2. Enable **ByteProof**.
3. Also check **Input Monitoring** and enable ByteProof there if it appears.
4. If ByteProof is already listed but hotkeys still do not work, remove it with
   the `-` button, reopen the app, and re-enable it. (This often happens after
   app updates.)

### Windows

No special permissions are required for hotkeys or Word integration. Microsoft
Word must be installed and running with a document open.
