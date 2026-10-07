# ByteProof 2.4.0 release — Task Intent

## Requested outcome

After the Windows beta (`2.3.1-beta.7`) was published, the owner asked to
promote the work to the official release line and make the website show the
new version: "push it into the official new version, which will be 2.4.0."

## Goal

Ship **2.4.0** as the public release on every channel:

- version bump in `src/settings.py` and `version_info.txt`
- git tag `v2.4.0`, with the Windows installer job attaching
  `ByteProof_Windows.zip` and `ByteProof_Installer_x64.msix`
- the signed, notarized, stapled Apple Silicon DMG attached locally
- the GitHub release set to non-prerelease so `releases/latest` and the
  website's download buttons serve 2.4.0
- `byteproof-version.json` on the website advertising 2.4.0 with SHA-256
  checksums, so installed copies update through the app's own updater

## Content of the release

Everything since 2.3.0 that had been waiting locally plus the Windows fix:

- Windows Live Check runs in Microsoft Word, and the four global shortcuts
  work again
- macOS updates install themselves (download, swap, relaunch)
- the Purchase button opens Stripe Checkout directly
- Automation and Live Check settings pages cleaned up; suggestion card
  restyled
- Word reliability: a busy Word is retried instead of reporting an empty
  selection; comment-box document names match the real title
- a malformed settings file no longer stops the app from starting

## Constraints

- Intel DMG is skipped when Rosetta 2 is absent, as in 2.3.0; the feed omits
  the Intel URL and the website's Intel button keeps its older link.
- The tagged CI run's informational Windows test job is known to wedge; it
  does not gate the release, which is why the release steps below do not
  wait on it.
