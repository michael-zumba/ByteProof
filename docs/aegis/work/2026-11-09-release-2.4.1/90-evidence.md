# ByteProof 2.4.1 release — Evidence

## 1. Release commits and tag

```
$ git log --oneline --decorate -3
ff17b7a (HEAD -> main, tag: v2.4.1) ci: run the release gate on the supported macOS runner
4b42490 Release 2.4.1: Updates now keep your licence and Accessibility permission, ...
67bbae4 Stabilize 2.4.1 beta: permissions, Word comments, and update reliability

$ venv/bin/python scripts/check_version.py --tag v2.4.1
version markers agree: 2.4.1 (tuple (2, 4, 1, 0))

$ venv/bin/ruff check src tests
All checks passed!
```

Local gate before tagging:

```
=== test_hardening.py    224 passed
=== test_license_cycle.py  3 passed
=== test_live_preview.py 164 passed
=== test_smoke.py        113 passed
All test files passed.
```

## 2. macOS installers

```
$ shasum -a 256 ByteProof_Installer_AppleSilicon.dmg
b632bcd5966d4642709d1879d4dec1f28ecec26dfb5e428cdc0510702ee902ad

$ shasum -a 256 ByteProof_Installer_Intel.dmg
d1bf045566dfdaea27ba3c8b272420325d38197a537ed7fad24aa51cd066de56
```

Both DMGs were mounted and checked:

```
version: 2.4.1
Apple Silicon: Mach-O 64-bit executable arm64
Intel:         Mach-O 64-bit executable x86_64
signature-ok
accepted  source=Notarized Developer ID
origin=Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)
The stapler validate action worked.
```

Notary submissions:

```
Apple Silicon app:  1449cc22-a924-4874-bf30-85a6c0d8edf4  Accepted
Apple Silicon DMG:  be732527-ea54-456f-909c-e87dcc605b09  Accepted
Intel app:          8db7644b-985b-4a41-a476-5cb2b1ed4b2c  Accepted
Intel DMG:          ddbc513d-ad00-4d00-ba43-61fbae6a5c73  Accepted
```

The x86 build environment used the latest cryptography release with an
official universal2/Intel-compatible wheel (`48.0.1`); version 50.0.0 has no
Intel wheel and a locally built fallback failed to load against the framework
OpenSSL. The final Intel environment passed an Ed25519 sign/verify check and
the installer was rebuilt, signed and notarized after that check.

## 3. GitHub release assets

```
$ gh api repos/michael-zumba/ByteProof/releases/latest
tag_name: v2.4.1
name: ByteProof 2.4.1
prerelease: false
draft: false

ByteProof_Installer_AppleSilicon.dmg  34328074
ByteProof_Installer_Intel.dmg         37757349
ByteProof_Installer_x64.msix          50055463
ByteProof_Windows.zip                 49346416
```

Downloaded release hashes:

```
windows_url 81629deb96e2e9e2a164923e259a8aaa3f644708e0b7c79814265d70b1877296 49346416 bytes
MSIX        2f4ea2cc3b210f2f220f7ea4299506095b52bf0ded366a0915f7a8fb00f6fb25 50055463 bytes
```

Release page:
https://github.com/michael-zumba/ByteProof/releases/tag/v2.4.1

## 4. CI

The first run was cancelled by GitHub before a macOS runner was assigned
because `macos-14` was retired on 2026-11-02. No release existed at that
point, so the tag moved once to an infrastructure-only runner fix.

Final run `42548396109`, tag `v2.4.1`:

```
Tests (macos-latest)          success  1m37s  ← release gate
Windows installer (x64)       success  1m55s  ← ZIP + MSIX attached
Tests (windows-latest)        informational, cancellation requested
```

The informational Windows job's cancellation was requested after the gate and
packaging jobs had succeeded. As documented in the repository, a wedged test
runner can ignore cancellation and timer updates; it does not gate the
release.

## 5. Website and live update feed

Website commit `19621324`:

```
Publish ByteProof 2.4.1 downloads and update feed
```

GitHub Pages build `1353030368` finished with status `built`.

```
$ curl -fsSL https://www.bytemind.co.nz/byteproof-version.json
{
  "version": "2.4.1",
  "release_date": "2026-11-09",
  "macos_apple_silicon_url": ".../latest/download/ByteProof_Installer_AppleSilicon.dmg",
  "macos_intel_url": ".../latest/download/ByteProof_Installer_Intel.dmg",
  "windows_url": ".../latest/download/ByteProof_Windows.zip",
  "sha256": {
    "macos_apple_silicon_url": "b632bcd5...ee902ad",
    "macos_intel_url": "d1bf0455...066de56",
    "windows_url": "81629deb...1877296"
  }
}
```

The live `byteproof.html` has `releases/latest` links for Apple Silicon, Intel
and Windows. All three `releases/latest/download/...` URLs returned the
expected asset sizes.

All three live assets were downloaded again and matched the feed:

```
macos_apple_silicon_url b632bcd5...ee902ad 34328074 bytes
macos_intel_url         d1bf0455...066de56 37757349 bytes
windows_url             81629deb...1877296 49346416 bytes
live feed downloads verified
```

## 6. Update comparison and local install

```
current=2.4.0          update_available=True  latest=2.4.1
current=2.4.1-beta.7   update_available=True  latest=2.4.1
current=2.4.1          update_available=False latest=None

$ /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    /Applications/ByteProof.app/Contents/Info.plist
2.4.1

$ spctl -a -vvv -t exec /Applications/ByteProof.app
accepted  source=Notarized Developer ID

[15:05:16] APP: pill shown (success): You're up to date (v2.4.1).
```

The local install used the atomic swap script, so the running app was replaced
without reinstalling it over its existing bundle.
