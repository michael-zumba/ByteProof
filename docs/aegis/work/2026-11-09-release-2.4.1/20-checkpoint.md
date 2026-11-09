# ByteProof 2.4.1 release — Checkpoint

## Released

The owner-confirmed `2.4.1-beta.7` work is public as **ByteProof 2.4.1**.
The final tag is `v2.4.1` at commit `ff17b7a`, and GitHub marks the release as
latest and non-prerelease.

The release contains the permission/licence persistence fixes, safe Word
comment editing, the comment-box false-positive fix, performance work, and
the update-race fixes tested in the beta.

## Release plumbing

The first tagged run was cancelled before a VM started because GitHub retired
the `macos-14` runner image on 2026-11-02. No GitHub release had been created,
so the unpublished tag was moved once to an infrastructure-only commit that
changes the gate to the supported `macos-latest` runner. The application
version and packaged source were unchanged.

## Published channels

- GitHub release with Apple Silicon DMG, Intel DMG, Windows ZIP and MSIX.
- Signed, notarized and stapled Apple Silicon and Intel installers.
- Website download buttons all use `releases/latest`.
- Live update feed advertises 2.4.1 with checksums for macOS Apple Silicon,
  macOS Intel and Windows.
- ByteProof 2.4.1 installed at `/Applications` for the owner.

## Verification

The macOS gate passed, Windows packaging passed, the live feed and every
downloaded asset checksum match, and the app's update comparison offers 2.4.1
to the beta while treating 2.4.1 itself as current.
