# Evidence — 2026-10-05

## The gate, after every change

```
$ venv/bin/python -m ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen BYTEPROOF_CI_PROGRESS=1 \
    venv/bin/python scripts/run_tests_ci.py tests/test_*.py
=== tests/test_hardening.py      209 passed in 30.01s
=== tests/test_license_cycle.py    2 passed in  2.44s
=== tests/test_live_preview.py   155 passed in  6.03s
=== tests/test_smoke.py          110 passed in 13.87s
All test files passed.

$ venv/bin/python scripts/check_version.py
version markers agree: 2.3.1-beta.5 (tuple (2, 3, 1, 5))
```

Baseline before the changes, for comparison: 204 + 2 + 155 + 110 = 471
passed; the new suite has 476.

## The unattended updater rehearsal (real DMG, real /Applications)

```
$ venv/bin/python  # stage_macos_payload + stage_macos_update against the
                   # notarized DMG and a stub bundle in /Applications
staging: /Applications/ByteProof-Rehearsal.app.update-staging exists: True
result: {'status': 'ok', 'message': 'ByteProof was updated and relaunched.',
         'version': 'rehearsal'}
installed rehearsal version: 2.3.1-beta.5
quarantine: '' rc 1            # no quarantine flag left on the new bundle
leftover backup: False staging: False
```

The rehearsal bundle was deleted afterwards. No ByteProof DMG was left
mounted, and the support folder holds no staging leftovers.

## The two mock runs (2026-10-05, after the redesign)

```
$ venv/bin/python scripts/mock_update_and_install.py
mock update+install: 16/16 checks passed

$ venv/bin/python scripts/mock_ui_update.py
  [PASS] the app process exits into the helper
  [PASS] the helper reports a result after the app exits
  [PASS] the result is ok — ok
  [PASS] the mock bundle is now the new version — 9.9.9
  [PASS] the new bundle was relaunched
  [PASS] no staging or backup is left behind
  [PASS] the real /Applications copy was never touched — 2.3.1-beta.6 -> 2.3.1-beta.6

mock UI update: 7/7 checks passed
```

The pipeline mock covers download + SHA-256 (including refusing a wrong
checksum), staging, the wait-for-exit helper, the swap, the quarantine clear,
the relaunch marker, and the three failure branches. The UI mock drives the
real `_handle_download_finished()` path in a real `ProofreaderApp` and asserts
the real install was untouched.

## beta.6, and the running-process trap

```
$ defaults read /Applications/ByteProof.app/Contents/Info.plist CFBundleShortVersionString
2.3.1-beta.6
$ codesign --verify --deep --strict /Applications/ByteProof.app && echo verified
codesign: verified
$ spctl --assess --type execute -v /Applications/ByteProof.app
/Applications/ByteProof.app: accepted
source=Notarized Developer ID
$ lsof -p <pid> | awk '$4=="txt" {print $NF}' | head -1
/Applications/ByteProof.app/Contents/MacOS/ByteProof
```

The packaged `src.app_version` module was extracted from the installed binary
and contains `update-staging`, `update-backup` and the `SKIP_NOTIFY` guard, so
the installed app is the final code. `settings.json` and the bundle both
report `2.3.1-beta.6`; the previous betas are archived in
`previous-versions/`.

After the beta.5 -> beta.6 install, `lsof` showed the running process was
executing the *archived beta.5* binary: the quit Apple Event had not been
processed while the app was in use, the bundle was moved anyway, and `open`
re-activated the old process. The app was quit for real (SIGTERM fallback),
relaunched from `/Applications`, and verified by `lsof`. This was an
install-procedure trap, not an update-flow defect; the app's own updater calls
`QApplication.quit()` itself and the helper waits for its pid to disappear.

## The beta build and install

```
$ ./build_macos.sh
notarytool: status: Accepted
The staple and validate action worked!
Build complete! Installer: ByteProof_Installer_AppleSilicon.dmg
Architecture: arm64

$ osascript -e 'quit app "ByteProof"'          # beta.4 quits first
$ mv "/Applications/ByteProof.app" \
     "$HOME/Library/Application Support/ByteMind/ByteProof/previous-versions/ByteProof_2.3.1-beta.4.app"
$ /usr/bin/ditto dist/ByteProof.app /Applications/ByteProof.app
$ /usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" \
      "/Applications/ByteProof.app/Contents/Info.plist"
2.3.1-beta.5
$ codesign --verify --deep --strict /Applications/ByteProof.app
codesign: verified
$ spctl --assess --type execute -v /Applications/ByteProof.app
/Applications/ByteProof.app: accepted
source=Notarized Developer ID
$ pgrep -fl "/Applications/ByteProof.app/Contents/MacOS/ByteProof"
70250 /Applications/ByteProof.app/Contents/MacOS/ByteProof
```

The packaged `src.app_version` module inside the installed binary was
extracted from the PyInstaller archive and contains the new staging code
(`.update-staging`), so the installed app is provably the redesigned build,
not a stale one.

## The owner's failure screenshot

The dialog the owner sent ("ByteProof could not install the update
automatically") is the roll-back branch of the first helper design. Nothing
on this machine recorded a helper run (no `update/update.log`, no
`update-result.json`, no mounted DMG, installed app still beta.4), so the
screenshot came from an earlier install attempt, not from this build. The
redesign removes the failure's precondition: the copy now happens while the
app is still running, and the headless step is two atomic renames.

## Renders

- `automation-before.png`, `automation-after.png`
- `panel-before.png`, `panel-after.png`

Rendered offscreen from the same synthetic content with the same window size
(no user text), as with the 2026-09-30 panel record.

## Not done

- No commit, no push, no public release, no update-feed change.
- The next real DMG update (a version newer than the feed's 2.3.0) is the
  live proof of the unattended path; the shell-level rehearsal covers every
  step except the app's own TCC identity.
