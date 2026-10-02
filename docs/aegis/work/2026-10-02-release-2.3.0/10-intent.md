# ByteProof 2.3.0 release — Task Intent

## Requested outcome

The owner said "Public release for me please. All good now." after the live
Stripe licensing cutover had been verified end to end (purchase, activation,
two-computer limit, portal, refund revocation). Under the release policy this
is the explicit instruction to pack and release.

## Goal

Ship **2.3.0** — the Stripe licensing change plus the 2.2.3 beta fixes — on
every public channel: the GitHub release (macOS and Windows installers), the
website's update feed, and the version markers. No product-code changes
beyond the release commit.

## Success evidence

- Version markers agree on 2.3.0, ruff clean, 466 tests green locally, and
  the macOS CI gate passed on the tagged commit.
- Tag `v2.3.0` points at the release commit; the Windows job attached
  `ByteProof_Windows.zip` and `ByteProof_Installer_x64.msix`.
- The Apple Silicon DMG built, notarised (accepted), stapled, mounts as
  2.3.0, and Gatekeeper accepts both the DMG and the app inside it.
- The live feed advertises 2.3.0 with SHA-256 checksums that match the
  uploaded artifacts, and the app's own updater downloads the DMG and
  verifies that checksum.

## Known gap

The Intel DMG was skipped: the x86_64 build needs Rosetta 2, which is not
installed on the release Mac. 2.2.2 shipped without an Intel DMG as well, the
feed has never served an Intel URL, and the website's Intel button still
points at v2.1.0, so nothing is broken. Installing Rosetta and re-running
`scripts/release.sh 2.3.0` attaches one.
