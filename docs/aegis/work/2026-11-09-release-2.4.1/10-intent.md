# ByteProof 2.4.1 release — Task Intent

## Requested outcome

The owner reviewed the current `2.4.1-beta.7` build and said it is stable,
then explicitly asked to "push the current byteproof beta version to the
official version and update the website and related download link."

## Goal

Promote the beta work to **ByteProof 2.4.1** on every public channel:

- a non-prerelease `v2.4.1` GitHub release, which becomes the target of the
  website's `releases/latest` download links;
- signed, notarized, stapled Apple Silicon and Intel DMGs, plus the Windows
  ZIP and MSIX built by tagged CI;
- `byteproof-version.json` advertising 2.4.1 with checksums that match the
  released installers, so installed copies update through the app;
- website download links pointing at the current release, including replacing
  the stale Intel link if a fresh Intel asset is available.

## Content of the release

Everything tested in the `2.4.1-beta.7` line:

- updates preserve the licence and Accessibility permission; a licence bound
  to the old network-based fingerprint moves to the stable hardware identity;
- Save in Settings no longer warns about shortcuts that were not changed;
- typing in a Word comment box is no longer interrupted, and Apply/Undo can
  correct only the selected words inside the comment;
- manual proofreading no longer mistakes ordinary Word text fields for an open
  comment composer;
- Word polling, Accessibility caching and Settings opening are faster; a
  cancelled preview no longer disables Live Check, and a second bundled copy
  refuses to run;
- repeated update installs can no longer race over one staging payload or
  report a false failure after a successful update.

## Constraints

- Keep the release non-prerelease so GitHub's `latest` endpoints switch to
  `v2.4.1`.
- Publish only after the macOS test gate and Windows packaging job pass.
- Verify the live feed and its checksums after the website deploy.
- Record release evidence and leave the installed app on 2.4.1 for the owner.
