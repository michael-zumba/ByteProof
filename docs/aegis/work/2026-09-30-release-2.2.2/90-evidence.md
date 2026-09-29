# Official 2.2.2 release — Evidence

## 1. Gate, on the released commit (11ce890)

```
$ venv/bin/python scripts/check_version.py
version markers agree: 2.2.2 (tuple (2, 2, 2, 0))
$ venv/bin/python -m ruff check src tests
All checks passed!
$ BYTEPROOF_CI_PROGRESS=1 QT_QPA_PLATFORM=offscreen \
      venv/bin/python scripts/run_tests_ci.py tests/test_*.py
============================= 194 passed in 28.75s =============================
============================= 149 passed in 2.22s ==============================
============================= 110 passed in 14.04s =============================
All test files passed.                            (exit 0)
```

## 2. Branches, tag, release commit

```
$ git push origin beta/2.2.2        # 10eede9..25bafd9
$ git checkout main && git merge --ff-only beta/2.2.2
Updating 30b1e9a..25bafd9  (fast-forward)
$ python3 tools/bump_version.py 2.2.2 "<notes>"       # settings, metadata, feed, example
$ git commit -m "Release 2.2.2: ..."                  # 11ce890
$ git tag -a v2.2.2 -m "ByteProof 2.2.2 - ..."
$ git push origin main                              # 25bafd9..11ce890
$ git push origin v2.2.2                            # new tag
$ git rev-parse v2.2.2^{commit}
11ce890ccbc03f76c5289c50460a95f4fb4dd095
```

## 3. CI (tag run 36629403388)

```
$ gh run view 36629403388 --repo michael-zumba/ByteProof \
      --json jobs --jq '.jobs[] | [.name, .conclusion] | @tsv'
Tests (macos-14)                          success
Windows installer (x64)                   success
Tests (windows-latest, informational)     in progress at record time
```

## 4. Release assets

```
$ gh release view v2.2.2 --repo michael-zumba/ByteProof \
      --json assets --jq '.assets[] | [.name, .size] | @tsv'
ByteProof_Installer_AppleSilicon.dmg      34273467
ByteProof_Installer_x64.msix              49563723
ByteProof_Windows.zip                     48860714

$ gh release download v2.2.2 ... --dir <tmp> && shasum -a 256 <tmp>/*
e70cfff6ec39ec5eb1c203865820270cdacbfb973d1a16a971e28a95080ef9fe  ByteProof_Installer_AppleSilicon.dmg
acae8835f6a3645f7c4667e716197a264c40adb15433ad3602e76707b007e7c7  ByteProof_Windows.zip
```

## 5. The DMG itself

```
$ ./build_macos.sh arm64
notarytool: id d2739c1f-25f6-4773-8939-bd0b8dcb9800  status: Accepted
stapler: The staple and validate action worked!

$ hdiutil attach -nobrowse -readonly ByteProof_Installer_AppleSilicon.dmg
$ defaults read "/Volumes/ByteProof Installer/ByteProof.app/Contents/Info.plist" CFBundleShortVersionString
2.2.2
$ codesign --verify --deep --strict "/Volumes/ByteProof Installer/ByteProof.app"
SIGNATURE_OK
$ spctl -a -vvv -t exec "/Volumes/ByteProof Installer/ByteProof.app"
accepted  source=Notarized Developer ID
origin=Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)
$ xcrun stapler validate ByteProof_Installer_AppleSilicon.dmg
The validate action worked!
```

## 6. Live feed and the app's own updater

```
$ curl -fsSL https://www.bytemind.co.nz/byteproof-version.json
{
  "version": "2.2.2",
  "release_date": "2026-09-29",
  "release_notes": "The suggestion panel now reads as clean cards ...",
  "macos_apple_silicon_url": ".../latest/download/ByteProof_Installer_AppleSilicon.dmg",
  "windows_url": ".../latest/download/ByteProof_Windows.zip",
  "sha256": {
    "macos_apple_silicon_url": "e70cfff6...80ef9fe",
    "windows_url": "acae8835...07b007e7c7"
  }
}

$ curl -sIL .../latest/download/ByteProof_Installer_AppleSilicon.dmg | tail
HTTP/2 302 ... HTTP/2 200  content-length: 34273467
$ curl -sIL .../latest/download/ByteProof_Windows.zip | tail
HTTP/2 302 ... HTTP/2 200  content-length: 48860714
$ gh api repos/michael-zumba/ByteProof/releases/latest --jq '.tag_name,.prerelease,.draft'
v2.2.2
false
false

$ venv/bin/python -c "...src.app_version..."
current=2.2.1:         update_available=True  latest=2.2.2
current=2.2.2-beta.6:  update_available=True  latest=2.2.2
download_update(feed, tmp):
  download ok: .../ByteProof_Installer_AppleSilicon.dmg
  sha256: e70cfff6ec39ec5eb1c203865820270cdacbfb973d1a16a971e28a95080ef9fe
```

The last block is the shipped update path itself: the same code an installed
copy runs fetched the feed, refused nothing, downloaded the DMG, and the
checksum matched the published value.
