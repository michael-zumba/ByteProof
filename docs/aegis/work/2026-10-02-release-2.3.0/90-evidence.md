# ByteProof 2.3.0 release — Evidence

## 1. Release commit and tag

```
$ git log --oneline -1
043dbb1 Release 2.3.0: Licensing now runs through Stripe: one key covers two
        computers, a licence portal moves ByteProof between them, …
$ git tag --list 'v2.3*'
v2.3.0
$ python scripts/check_version.py
version markers agree: 2.3.0 (tuple (2, 3, 0, 0))
```

## 2. Locally built and verified macOS installer

```
$ ./scripts/release.sh 2.3.0 "Licensing now runs through Stripe: …"
notarytool: id 5e06b952-8906-4249-af60-82696e159dbf  status: Accepted
stapler: The staple and validate action worked!
Building Intel DMG … Skipped: Rosetta 2 is not installed

$ hdiutil attach -nobrowse -readonly ByteProof_Installer_AppleSilicon.dmg
$ defaults read "/Volumes/ByteProof Installer/ByteProof.app/Contents/Info.plist" CFBundleShortVersionString
2.3.0
$ codesign --verify --deep --strict "/Volumes/ByteProof Installer/ByteProof.app"
SIGNATURE_OK
$ spctl -a -vvv -t exec "/Volumes/ByteProof Installer/ByteProof.app"
accepted  source=Notarized Developer ID
origin=Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)
$ xcrun stapler validate ByteProof_Installer_AppleSilicon.dmg
The validate action worked!
```

## 3. Release assets

```
$ gh release view v2.3.0 --repo michael-zumba/ByteProof --json assets \
      --jq '.assets[] | "\(.name)\t\(.size)"'
ByteProof_Installer_AppleSilicon.dmg  34282412
ByteProof_Installer_x64.msix          49569793
ByteProof_Windows.zip                 48866450

$ shasum -a 256 ByteProof_Installer_AppleSilicon.dmg
64d6472d369b90cc9eaa317e017f32d4288702c7af420031d724283698c13e5b
$ shasum -a 256 <downloaded ByteProof_Windows.zip>
b273d31f17373c6eec72b97745673c0edd16aa587702d43a2e4410ee54d9c07a
```

## 4. CI

```
Tests (macos-14)             completed success      ← the release gate
Windows installer (x64)      completed success      ← attached both Windows assets
Tests (windows-latest)       informational, does not gate
```

## 5. Live feed and the app's own updater

```
$ curl -fsSL https://www.bytemind.co.nz/byteproof-version.json
{
  "version": "2.3.0",
  "release_date": "2026-10-02",
  "release_notes": "Licensing now runs through Stripe: …",
  "macos_apple_silicon_url": ".../latest/download/ByteProof_Installer_AppleSilicon.dmg",
  "windows_url": ".../latest/download/ByteProof_Windows.zip",
  "sha256": {
    "macos_apple_silicon_url": "64d6472d…c13e5b",
    "windows_url": "b273d31f…d9c07a"
  }
}

$ venv/bin/python - <<'PY'   # the shipped update path, run against the live feed
current=2.3.0-beta.1   update_available=True  latest=2.3.0
current=2.3.0          update_available=False latest=None
current=2.2.2          update_available=True  latest=2.3.0
downloaded ByteProof_Installer_AppleSilicon.dmg (34282412 bytes)
expected sha256: 64d6472d369b90cc9eaa317e017f32d4288702c7af420031d724283698c13e5b
actual   sha256: 64d6472d369b90cc9eaa317e017f32d4288702c7af420031d724283698c13e5b
UPDATE PATH OK: feed -> download -> checksum verified
```

## 6. Tooling fixed during the release

- `scripts/release.sh` no longer aborts when Rosetta is missing: it skips the
  Intel build with instructions instead of stopping after the DMG builds had
  already been made.
- The feed is now published **with** SHA-256 checksums (the script downloads
  the Windows zip and hashes both artifacts before committing the feed).
  Previously the feed went out with an empty `sha256` map, leaving the
  updater to trust the signature alone.
