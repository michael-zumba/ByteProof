# Unattended updates that stay unattended — Checkpoint

## What was wrong

The owner's dialog does not mean the update failed. In the log from
2026-10-07, the update **succeeded** and the dialog appeared anyway:

```
[17:28:50] UPDATE: update checksum verified: ByteProof_Installer_AppleSilicon.dmg
[17:28:59] UPDATE: unattended installer staged for /Applications/ByteProof.app
[17:29:39] UPDATE: update checksum verified: ...        <- the owner clicked Install again
[17:29:43] UPDATE: unattended installer staged for /Applications/ByteProof.app
...
update.log
2026-10-07 17:29:44 no staged bundle at /Applications/ByteProof.app.update-staging
2026-10-07 17:29:44 installed 2.4.0; relaunching
[17:29:50] APP: pill shown (success): ByteProof updated to 2.4.0.
```

Two install clicks 44 seconds apart started **two helpers over one shared
staging folder**. The newer helper installed the update; the older one woke
up, found its payload gone, and took the failure branch — which is the dialog
in the report, with the DMG opened on top. Everything about that branch was
correct except the conclusion: the update had landed.

The other two dialog sources in the log are transient and already gone: two
checksum mismatches at 17:02 while the 2.4.0 assets and the feed were still
being finalised on release day (the published feed checksum matches the
released DMG today — verified), and the genuine "cannot copy next to
/Applications" case, which stays as the last resort.

## What changed

* **One install at a time.** `stage_macos_update` writes
  `update/install.lock`, which the helper replaces with its own pid.
  `pending_update_install()` reports a live helper; the app checks it before
  staging, so a second click simply quits into the helper that is already
  waiting instead of starting a rival. A stale lock (dead pid) is removed.
* **A payload per attempt.** Staging is
  `ByteProof.app.update-staging-<id>`, the backup is
  `ByteProof.app.update-backup-<id>`, and the script is
  `install-macos-update-<id>.sh` — the old design wrote *one* script path that
  a second attempt could overwrite while the first helper was executing it.
  Older stagings are cleared only when a new attempt is allowed to start.
* **A lost race is not a failure.** If the helper finds no staging but the
  installed bundle already carries the version it was asked to install, it
  reports `ok` and touches nothing — no dialog, no DMG.
* The lock is released on every exit path (`trap ... EXIT`), so a crashed
  helper cannot block the next update.

## Verified

* The rehearsal (`scripts/mock_update_and_install.py`) passes 18/18, including
  the new checks: a vanished staging folder next to an *installed* target
  version reports success, and leaves no lock.
* A real unattended install on this machine: staged the beta.5 DMG next to
  `/Applications/ByteProof.app`, ran the generated helper, quit the running
  app, and watched the helper swap and reopen it — beta.4 → beta.5, no
  staging/backup/lock left, `update-result.json` = ok, and the app came back
  with its Accessibility permission intact.
* New tests: `test_two_install_clicks_each_get_their_own_payload`,
  `test_a_pending_install_is_detected_and_a_stale_one_is_not`,
  `test_the_helper_treats_an_already_installed_version_as_done`,
  `test_a_second_install_click_joins_the_waiting_helper`.

## What the owner sees now

Clicking Install: the pill says "Installing the update — ByteProof will reopen
automatically", the app quits, and it comes back on the new version with a
"ByteProof updated to …" message. Clicking Install twice makes the second
click join the first. The manual dialog now only appears if macOS genuinely
refuses the swap (for example a standard user account that cannot write to
/Applications).
