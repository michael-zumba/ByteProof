# Unattended updates that stay unattended — Evidence

## 1. The dialog, and the install that succeeded underneath it

```
$ sed -n 7540,7600p ~/Library/Application\ Support/ByteMind/ByteProof/capture.log
[17:28:50] UPDATE: update checksum verified: ByteProof_Installer_AppleSilicon.dmg
[17:28:59] UPDATE: unattended installer staged for /Applications/ByteProof.app
[17:28:59] APP: pill shown (success): Installing the update — ByteProof will reopen automatically.
[17:29:14] APP: activation policy menu-bar-only            <- app restarted by the owner
[17:29:31] APP: tray menu opened
[17:29:39] UPDATE: update checksum verified: ByteProof_Installer_AppleSilicon.dmg
[17:29:43] UPDATE: unattended installer staged for /Applications/ByteProof.app
[17:29:50] APP: pill shown (success): ByteProof updated to 2.4.0.

$ cat ~/Library/Application\ Support/ByteMind/ByteProof/update/update.log
2026-10-07 17:28:59 update to 2.4.0 staged
2026-10-07 17:29:43 update to 2.4.0 staged
2026-10-07 17:29:44 no staged bundle at /Applications/ByteProof.app.update-staging
2026-10-07 17:29:44 installed 2.4.0; relaunching
```

Two helpers, one staging path: the older one reported failure (and opened the
DMG - the dialog in the owner's screenshot) while the newer one installed the
update.

## 2. The feed checksum is correct today

```
$ curl -sS https://www.bytemind.co.nz/byteproof-version.json
{"version":"2.4.0", ... "sha256": {"macos_apple_silicon_url":
 "b86987cef4685e62b1456d466919b56f73233498251bc4869f1a2e4d2e1be71d"}}
$ curl -sSL -o /tmp/feed-macos.dmg <the release asset>
$ shasum -a 256 /tmp/feed-macos.dmg
b86987cef4685e62b1456d466919b56f73233498251bc4869f1a2e4d2e1be71d
```

So the two "checksum mismatch" lines at 17:02 were release-day state (the feed
and the assets were being finalised), not a standing bug.

## 3. The rehearsal, all checks

```
$ venv/bin/python scripts/mock_update_and_install.py
1. download + checksum verification           3/3
2. staging the payload next to the installed  4/4
3. the helper waits, swaps, relaunches        5/5
4. failure branches                           6/6
   - a vanished staging folder next to an *installed* target version now
     reports success and leaves no lock
mock update+install: 18/18 checks passed
```

## 4. A real unattended install, on /Applications

```
$ venv/bin/python /tmp/byteproof_real_update_rehearsal.py
before: version 2.4.1-beta.4 pids [80701]
staged: /Applications/ByteProof.app.update-staging-90671-1791429953
helper: .../update/install-macos-update-90671-1791429953.sh   (pid 90748)
asked the running app to quit
helper exit code: 0
after: version 2.4.1-beta.5 pids [90776]
leftover staging/backup folders: none
pending helper: None
update-result: {"status":"ok","message":"ByteProof was updated and relaunched.","version":"2.4.1-beta.5"}

$ tail -3 ~/Library/Application\ Support/ByteMind/ByteProof/capture.log
[16:26:07] LIVE PERMISSION: trusted=True app='ByteProof'
[16:26:10] APP: pill shown (success): ByteProof updated to 2.4.1-beta.5.
```

The helper's atomic swap also kept the Accessibility grant (trusted=True on
the relaunched build) and the app announced the new version by itself.

## 5. Tests

```
tests/test_hardening.py::test_two_install_clicks_each_get_their_own_payload
tests/test_hardening.py::test_a_pending_install_is_detected_and_a_stale_one_is_not
tests/test_hardening.py::test_the_helper_treats_an_already_installed_version_as_done
tests/test_hardening.py::test_a_second_install_click_joins_the_waiting_helper

$ BYTEPROOF_CI_PROGRESS=1 ./scripts/run_tests.sh
=== test_hardening.py       219 passed
=== test_license_cycle.py     3 passed
=== test_live_preview.py    164 passed
=== test_smoke.py           113 passed
All test files passed.
```

## 6. Beta

```
$ venv/bin/python tools/bump_version.py 2.4.1-beta.5 "Fix: updates install
  themselves again - two install clicks can no longer fight, and a helper that
  loses the race no longer reports a failure"
$ ./build_macos.sh        # notarised, stapled
$ # installed by the rehearsal above; /Applications/ByteProof.app reports
$ /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" /Applications/ByteProof.app/Contents/Info.plist
2.4.1-beta.5
```
