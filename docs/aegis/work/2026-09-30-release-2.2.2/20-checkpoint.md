# Official 2.2.2 release — Checkpoint

## What shipped

- **2.2.2 official**: `APP_VERSION` and the Windows metadata bumped from
  2.2.2-beta.6; release commit 11ce890 on main; annotated tag v2.2.2 -> 11ce890.
- main fast-forwarded from the 2.2.1 release (30b1e9a) to the beta tip
  (25bafd9), then the release commit; `beta/2.2.2` and `main` both pushed.
- **CI** (tag run 36629403388): the macOS test gate passed, and the Windows
  installer (x64) job published GitHub Release v2.2.2 with
  `ByteProof_Windows.zip` and `ByteProof_Installer_x64.msix`.
- **Apple Silicon DMG** built with `./build_macos.sh arm64`, notarized
  (accepted), stapled, and uploaded to the release.
- **Website feed**: `ByteMind_Website/byteproof-version.json` committed
  (ecb018b "ByteProof 2.2.2 update feed") and pushed; the live feed was
  verified serving 2.2.2 with its checksums.

## Assets and checksums

Checksums were computed from the assets downloaded back out of the GitHub
release — not from the local build directory — so they describe exactly what
a user downloads.

| Asset | Bytes | SHA-256 |
| --- | --- | --- |
| `ByteProof_Installer_AppleSilicon.dmg` | 34,273,467 | `e70cfff6ec39ec5eb1c203865820270cdacbfb973d1a16a971e28a95080ef9fe` |
| `ByteProof_Windows.zip` | 48,860,714 | `acae8835f6a3645f7c4667e716197a264c40adb15433ad3602e76707b007e7c7` |
| `ByteProof_Installer_x64.msix` | 49,563,723 | attached by CI; not advertised in the feed |

## Release notes (feed text)

> The suggestion panel now reads as clean cards with one Apply all button.
> The menu bar icon brings the window back, a live edit keeps its place and is
> never reported as a failure after it lands, and Mail no longer shows
> "No text selected" false alarms. Your keystrokes are no longer written to
> the support log.

`release_date` is written by `tools/bump_version.py` in UTC (2026-09-29); it is
2026-09-30 in Pacific/Auckland.

## Process note

`scripts/release.sh` builds both DMGs, and its Intel step cannot run on this
machine (Rosetta 2 missing), which would stop it before the upload and feed
steps. The release was therefore run as the script's own steps in order —
bump, commit, tag, push, build arm64 DMG, wait for CI, upload, publish the
feed — minus the Intel build, matching the documented 2.2.0/2.2.1 precedent.
Rosetta installation needs the owner's admin password and stays open as a
separate decision.

## Deliberately untouched

- The feed's URL keys: no `macos_intel_url`, on purpose (see intent).
- `byteproof-version.json.example` keeps `"sha256": {}` like v2.2.1's did; the
  example documents the shape, the live feed carries the release values.
- The owner's `/Applications` install: the app offers this update itself, and
  the beta bundle the owner is testing was left in place.
