# The Save-time shortcut warning — Evidence

## 1. The dialog, reproduced against the owner's own settings

```
$ QT_QPA_PLATFORM=offscreen venv/bin/python - <<'PY'
  settings = settings_mod.load_runtime_settings()
  print("live_toggle_hotkey:", settings["general"].get("live_toggle_hotkey"))
  hotkeys._running_app_hotkeys = explode   # raises if the scan is called
  dialog.accept(); print(f"unchanged Save took {ms:.2f} ms")
PY
live_toggle_hotkey: <cmd>+<shift>+l
Settings built in 325 ms; unchanged Save took 0.13 ms, no conflict scan, no dialog
```

Before the change, the same call reached `find_hotkey_conflicts()`, which
scanned the running apps' menu bars and found Finder -> Downloads for
`<cmd>+<shift>+l` - the sentence in the owner's screenshot.

## 2. The two code paths that made it wrong

```
$ sed -n 4806,4822p src/gui.py     # before
        if (
            self._hotkey_conflict_acknowledged          # set only by "Save Anyway"
            and current_hotkeys == self._original_hotkeys
        ):
            super().accept()
            return
        conflicts = self._hotkey_conflicts()            # ran on every save
```

and the filter, when a change was seen:

```
changed = {"live_toggle_hotkey"}      # settings key
only_labels = {"Turn Live Check on/off"}  # what find_hotkey_conflicts reports
```

which never matched. Both are fixed: one `_HOTKEY_LABELS` map now feeds both
sides, and the scan runs only when something changed.

## 3. Tests

```
tests/test_hardening.py::test_hotkey_conflicts_report_duplicates_and_system_keys   (existing)
tests/test_hardening.py::test_hotkey_conflicts_can_be_limited_to_the_changed_shortcut
tests/test_hardening.py::test_saving_settings_without_changing_a_shortcut_does_not_warn
tests/test_hardening.py::test_changing_a_shortcut_to_a_conflicting_one_still_warns

$ venv/bin/python -m pytest tests/test_hardening.py \
      -k "hotkey_conflict or changing_a_shortcut or saving_settings_without" -q
4 passed

$ BYTEPROOF_CI_PROGRESS=1 ./scripts/run_tests.sh
=== test_hardening.py       222 passed
=== test_license_cycle.py     3 passed
=== test_live_preview.py    164 passed
=== test_smoke.py           113 passed
All test files passed.
```

## 4. Beta

```
$ venv/bin/python tools/bump_version.py 2.4.1-beta.6 "Fix: Settings only warns
  about a shortcut you are changing - saving no longer nags about keys already
  in use"
$ ./build_macos.sh            # notarised, stapled
$ venv/bin/python /tmp/byteproof_real_update_rehearsal.py
before: version 2.4.1-beta.5 pids [90776]
after:  version 2.4.1-beta.6 pids [278]
leftover staging/backup folders: none
update-result: {"status":"ok","message":"ByteProof was updated and relaunched.","version":"2.4.1-beta.6"}
$ tail -2 ~/Library/Application Support/ByteMind/ByteProof/capture.log
[16:38:54] LIVE PERMISSION: trusted=True app='ByteProof'
[16:38:57] APP: pill shown (success): ByteProof updated to 2.4.1-beta.6.
```
