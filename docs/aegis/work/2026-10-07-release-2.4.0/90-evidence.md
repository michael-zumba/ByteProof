# ByteProof 2.4.0 release — Evidence

## 1. Release commit and tag

```
$ git log --oneline -1
7638058 Release 2.4.0: Windows Live Check in Word and working shortcuts,
        self-installing macOS updates, and a cleaner settings experience
$ git tag --list 'v2.4*'
v2.4.0
$ python3 scripts/check_version.py --tag v2.4.0
version markers agree: 2.4.0 (tuple (2, 4, 0, 0))
```

Local gate before tagging: `ruff` clean; `pytest` on the four test files
481 passed (only the known combined-run offscreen animation flake was
deselected; it passes per-file, which is how CI runs it).

## 2. macOS installer, built and verified locally

```
$ ./build_macos.sh arm64
Notarizing installer with Apple notary service...
  id: 98472502-40ed-4fbc-88cb-66a9396c752a
  status: Accepted
Stapling notarization ticket to installer... The staple and validate action worked!
Build complete! Installer: ByteProof_Installer_AppleSilicon.dmg

$ hdiutil attach -nobrowse -readonly ByteProof_Installer_AppleSilicon.dmg
$ defaults read "/Volumes/ByteProof Installer/ByteProof.app/Contents/Info.plist" CFBundleShortVersionString
2.4.0
$ codesign --verify --deep --strict "/Volumes/ByteProof Installer/ByteProof.app"
SIGNATURE_OK
$ spctl -a -vvv -t exec "/Volumes/ByteProof Installer/ByteProof.app"
accepted  source=Notarized Developer ID
origin=Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)
$ xcrun stapler validate ByteProof_Installer_AppleSilicon.dmg
The validate action worked!
$ shasum -a 256 ByteProof_Installer_AppleSilicon.dmg
b86987cef4685e62b1456d466919b56f73233498251bc4869f1a2e4d2e1be71d
```

Intel DMG skipped: Rosetta 2 is not installed (same as 2.3.0). The feed does
not advertise an Intel URL.

## 3. Release assets

```
$ gh release view v2.4.0 --json assets --jq '.assets[] | "\(.name)\t\(.size)"'
ByteProof_Installer_AppleSilicon.dmg  34298830
ByteProof_Installer_x64.msix          49582474
ByteProof_Windows.zip                 48878769
```

The Windows ZIP's byte-for-byte checksum, hashed from the release asset:
`bc3138eafe450c663f5420d55404148e2f29b563621e843f753d03b49a9c1113`.

The release is non-prerelease and is GitHub's latest:

```
$ gh release view --json tagName,isPrerelease
{"latest":"v2.4.0","prerelease":false}
```

Release page: https://github.com/michael-zumba/ByteProof/releases/tag/v2.4.0
(the name and user-facing body were set with `gh release edit`, as for
2.3.0).

## 4. CI

```
Tests (macos-14)             completed success   ← the release gate
Windows installer (x64)      completed success   ← attached the ZIP and MSIX
Tests (windows-latest)       informational, wedged as always; does not gate
```

After the macOS gate and the installer job had finished, the cancel for the
held run was requested so the wedged runner is not kept for hours.

## 5. Live website feed and the app's own updater

```
$ curl -fsSL https://www.bytemind.co.nz/byteproof-version.json
{
  "version": "2.4.0",
  "release_date": "2026-10-07",
  "release_notes": "Windows now has Live Check in Microsoft Word — …",
  "macos_apple_silicon_url": ".../latest/download/ByteProof_Installer_AppleSilicon.dmg",
  "windows_url": ".../latest/download/ByteProof_Windows.zip",
  "sha256": {
    "macos_apple_silicon_url": "b86987ce…e1be71d",
    "windows_url": "bc3138ea…a9c1113"
  }
}
```

Website commit: `c57831f` "ByteProof 2.4.0 update feed". No other
version string on the website was stale (the page buttons use
`releases/latest`, which now resolves to v2.4.0).

The shipped update path, run against the live feed:

```
current=2.3.0          update_available=True   latest=2.4.0
current=2.3.1-beta.7   update_available=True   latest=2.4.0
current=2.4.0          update_available=False  latest=None
downloaded ByteProof_Installer_AppleSilicon.dmg
expected sha256: b86987cef4685e62b1456d466919b56f73233498251bc4869f1a2e4d2e1be71d
actual   sha256: b86987cef4685e62b1456d466919b56f73233498251bc4869f1a2e4d2e1be71d
UPDATE PATH OK: feed -> download -> checksum verified
```
