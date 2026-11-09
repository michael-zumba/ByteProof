# Safari editing, and the licence/permission re-entry — Evidence

## 1. The Safari apply, before and after

Same machine, same app, same day. Before the installs (2.4.0 running):

```
[12:52:57] APP: pill shown (processing): Polishing 590 characters from Safari…
[12:52:59] APP: pill shown (success): Applied to Safari. Press Cmd/Ctrl+Z to undo.
[13:04:17] APP: pill shown (success): Applied to Safari. ...
[13:07:15] APP: pill shown (success): Applied to Safari. ...
[13:41:47] APP: pill shown (success): Applied to Safari. ...
```

After the 14:50/15:00 installs (beta, grant revoked and re-added while it ran):

```
[15:04:14] APP: pill shown (processing): Applying to Safari…
[15:04:16] mac_replace: paste was not observed in the target; reporting for review
[15:04:16] APP: pill shown (error): Could not confirm the paste — please check the document.
[15:04:40] LIVE APPLY ALL: app='Safari' has_range=True count=3
[15:04:41] ax_replace_range verify: selected text <len=32 ...> expected <len=40 ...>
[15:04:41] ax_replace_range paste verify: mismatch expected=<len=40 ...>
[15:04:43] ax_replace_range: retrying paste via System Events
[15:04:44] ax_replace_range: put the previous selection back
[15:04:44] LIVE APPLY ALL: skipping a span after failure ('Could not apply the edit in this app.')
```

The AX *range* write took (the selection line shows the original 32 characters
selected) while the Command-V never landed - reading worked, posting did not.

The permission log either side of the installs:

```
[14:32:23] LIVE PERMISSION: trusted=True app='Safari'
[14:51:32] LIVE PERMISSION: trusted=False app='ByteProof'   <- after ditto install
[14:52:31] LIVE PERMISSION: trusted=True app='ByteProof'    <- after re-grant
[15:00:51] LIVE PERMISSION: trusted=False app='ByteProof'   <- after second ditto install
[15:03:36] LIVE PERMISSION: trusted=True app='System Settings'
```

## 2. Merge copy vs atomic swap, measured

```
$ ditto dist/ByteProof.app /Applications/ByteProof.app     # merge into the bundle
   ... relaunch ...
[15:00:51] LIVE PERMISSION: trusted=False app='ByteProof'

$ ditto dist/ByteProof.app /Applications/ByteProof.app.update-staging
$ mv /Applications/ByteProof.app /Applications/ByteProof.app.update-backup
$ mv /Applications/ByteProof.app.update-staging /Applications/ByteProof.app
   ... relaunch ...
[15:29:27] LIVE PERMISSION: trusted=True app='ByteProof'
```

`scripts/install_local_beta.sh` now does the second shape (and verified the
same way on a second run: `[15:30:10] trusted=True`).

## 3. The Safari apply again, with the fixed install

A scratch page (`/tmp/byteproof-paste-test.html`, a textarea with "I recieve
teh manuscript and it was definately written yesterday.", opened in Safari and
closed again afterwards), the whole sentence selected through Accessibility,
the app's own proofread hotkey:

```
$ osascript -e 'tell application "System Events" to keystroke "\'" using {command down, shift down}'
[15:31:11] APP: pill shown (processing): Polishing 65 characters from Safari…
[15:31:12] APP: pill shown (processing): Applying to Safari…
[15:31:13] APP: pill shown (success): Applied to Safari. Press Cmd/Ctrl+Z to undo.
```

"Applied" is the app's verified verdict (`_mac_replace` only reports it after
it reads the pasted text back out of the field).

## 4. The licence fingerprint, on this machine

```
$ venv/bin/python -c "...uuid.getnode(), platform.node(), IOPlatformUUID..."
uuid.getnode: 200850841586891   b6:ac:3b:10:cc:cb   # first NIC
platform.node(): MacBook-Pro-11.local
IOPlatformUUID: 6B547C38-CE29-5C29-A66A-68023DF639D7
ifconfig ethers: 15 interfaces, several regenerated per session
```

Before the fix, all three went into the hash, so the next network change moved
it. The service's own counters agreed with the churn:

```
$ curl -s https://api.bytemind.co.nz/health
{"status":"ok", ..., "licenses":3, "activations":5}
$ # validate with the stored purchase key and the (old) stored fingerprint
{"ok": true, "valid": true, "device_count": 4, "device_limit": null}
```

Four activations for one computer. After the migration:

```
stored machine_fp: SalaJZBb7FxfTqqv4yQXwA==      # stable hardware identity
stable fingerprint now: SalaJZBb7FxfTqqv4yQXwA==
legacy fingerprint: NiG84Yq_LMBeBdgVFiQiIw==
stable fingerprint valid: True   | device_count: 4   # unchanged
legacy fingerprint valid: False  | reason: not_activated   # old seat released
```

## 5. Tests

```
tests/test_smoke.py::test_machine_fingerprint_ignores_the_network_identity
tests/test_license_cycle.py::test_a_licence_bound_to_the_old_fingerprint_moves_to_the_stable_one
tests/test_live_preview.py::test_a_mid_run_permission_grant_restarts_the_app_once
```

plus the updated readiness-row test (the action is now "Enable").

```
$ BYTEPROOF_CI_PROGRESS=1 ./scripts/run_tests.sh
=== test_hardening.py        215 passed
=== test_license_cycle.py      3 passed
=== test_live_preview.py     161 passed
=== test_smoke.py            112 passed
All test files passed.
```

## 6. Beta

```
$ venv/bin/python tools/bump_version.py 2.4.1-beta.2 "Fix: the licence stays
  put across updates and network changes, and Accessibility is re-granted the
  way macOS remembers"
$ ./scripts/install_local_beta.sh
Installed ByteProof 2.4.1-beta.2 (signature verified).
ByteProof is running. Its Accessibility permission was left in place.
$ /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" /Applications/ByteProof.app/Contents/Info.plist
2.4.1-beta.2
$ codesign --verify --deep --strict /Applications/ByteProof.app   # silent
```

The notarized DMG (`ByteProof_Installer_AppleSilicon.dmg`, accepted and
stapled) is built from the same tree.
