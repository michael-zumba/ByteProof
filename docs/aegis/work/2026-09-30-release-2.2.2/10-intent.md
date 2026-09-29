# Official 2.2.2 release — Task Intent

## Requested outcome

The owner confirmed the current beta (2.2.2-beta.6) is good and asked for the
official release so every installed copy receives it.

## Goal

Ship 2.2.2 as the official release: version bump, tag, GitHub release with the
installers, and the public update feed — the release policy's "owner
explicitly instructs to pack and release" case. No product-code changes beyond
the version markers and the feed.

## Success evidence

- Version markers agree on 2.2.2 (`scripts/check_version.py`), ruff clean, and
  the full macOS gate green on the exact released commit.
- Tag v2.2.2 points at the release commit; the GitHub Actions macOS gate
  passed and the Windows installer job published the Windows assets.
- The Apple Silicon DMG built locally, was notarized (accepted) and stapled,
  verifies as 2.2.2 with a Developer ID signature, and is uploaded to the
  release.
- The live website feed advertises 2.2.2 with SHA-256 checksums that match the
  uploaded artifacts, and the app's own updater downloads and verifies it
  from that feed.

## Stop condition

- `done`: `byteproof-version.json` serves 2.2.2 and the release carries the
  installers; an installed 2.2.1 — and the owner's 2.2.2-beta.6 — are offered
  the update.

## Non-goals

- No product changes: only version markers, the feed, and this record.
- Intel DMG: Rosetta 2 is still not installed on the release machine, and
  installing it needs the owner's admin password. The feed keeps no
  `macos_intel_url` (same as 2.2.0/2.2.1), so Intel users are never offered a
  download that would 404.
