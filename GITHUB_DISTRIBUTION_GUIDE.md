# How to Build and Distribute "ByteProof" App

This guide explains how to ship a new ByteProof release from your Mac: it
builds the signed/notarized macOS DMGs locally, kicks off the Windows build
(ZIP + Microsoft Store MSIX) in GitHub Actions at the same time, uploads
everything to a GitHub Release, and then publishes the update feed to the
ByteMind website.

## One-command release (recommended)

**Prerequisites:**

- Mac with Apple Silicon + Rosetta 2, Python 3.13, and `create-dmg`
  (`brew install create-dmg`).
- GitHub CLI installed and authenticated: `gh auth login`.
- The `.venv` (Apple Silicon) and `.venv_x86` (Intel) Python environments from
  the setup below.
- Optional: Apple Developer ID / notary credentials so the DMGs are notarized
  (set `BYTEPROOF_DEV_ID`; see `scripts/notarize.sh`).

Release a new version with one command:

```bash
./scripts/release.sh 1.6.0 "Short release notes for users"
```

What happens automatically:

1. `tools/bump_version.py` updates the app version, the Windows file metadata,
   and the website update feed.
2. The script commits, tags (`v1.6.0`), and pushes — GitHub Actions immediately
   starts building `ByteProof_Windows.zip` on a Windows runner.
3. While GitHub builds the Windows ZIP and MSIX, the script builds
   `ByteProof_Installer_AppleSilicon.dmg` and `ByteProof_Installer_Intel.dmg`
   on your Mac.
4. When both are finished, it uploads the two DMGs to the GitHub Release, which
   already contains the Windows zip.
5. Only then does it push `byteproof-version.json` to the ByteMind website
   repository. GitHub Pages publishes it, and installed copies of ByteProof
   see the new version on their next update check.

If any step fails, fix the problem and run the same command again. Existing
tags are detected and skipped, so re-runs continue without re-bumping the
version.

## Manual builds (fallback)

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

### 3. Build for Windows (manual)

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

## GitHub Releases

The recommended `release.sh` flow creates the release automatically. If you
prefer to do it by hand:

1. Push a version tag to GitHub:
   ```bash
   git tag v1.6.0
   git push origin v1.6.0
   ```
2. GitHub Actions builds `ByteProof_Windows.zip` and attaches it to a new
   release with that tag.
3. Upload the two DMGs to the same release:
   ```bash
   gh release upload v1.6.0 ByteProof_Installer_AppleSilicon.dmg ByteProof_Installer_Intel.dmg
   ```
4. Publish the release.

**Latest release always:**
`https://github.com/michael-zumba/ByteProof/releases/latest`

**Direct links (stable across releases):**
- **Mac (Apple Silicon):** `https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Installer_AppleSilicon.dmg`
- **Mac (Intel):** `https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Installer_Intel.dmg`
- **Windows:** `https://github.com/michael-zumba/ByteProof/releases/latest/download/ByteProof_Windows.zip`

## Microsoft Store (Windows)

Each release also produces `ByteProof_Installer_x64.msix` for the Microsoft
Store. Microsoft signs the package during certification, so Store users
install without SmartScreen warnings and no paid certificate is required.

Before the MSIX is built, set the Store identity in
`packaging/windows/msix-config.json` (get the values from Partner Center →
Product management → Product identity). Until then, releases ship the ZIP
only. Full instructions: [MICROSOFT_STORE_GUIDE.md](MICROSOFT_STORE_GUIDE.md).

---

## In-App Updates

ByteProof checks `https://www.bytemind.co.nz/byteproof-version.json` (defined in
`src/app_version.py`) shortly after launch. The file lives in the
`michael-zumba/bytemind-website` repository and is pushed there automatically by
the release script. A copy of the expected shape is included in the ByteProof
repo as `byteproof-version.json.example`:

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
