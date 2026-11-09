# The Save-time shortcut warning — Checkpoint

## What was wrong

The prompt was meant to appear when the user *sets* a shortcut that another
app or action already owns. Three things stopped that being true:

1. **The "only when it changed" guard did not work.** `accept()` short-circuited
   only if the user had already chosen "Save Anyway" *in that dialog session*
   **and** the shortcuts were unchanged. On the first Save of any session the
   flag was false, so an unchanged form fell through to the conflict check and
   warned about whatever the four shortcuts happened to be - which is the
   owner's "every time I click save".
2. **The conflict scan is a live menu-bar scan** of every running app (up to
   1.5 s, in a worker). It ran on every Save, so the same unchanged shortcut
   could even produce *different* messages as apps came and went - the owner's
   `Cmd+Shift+L` matched Finder's Downloads, so the warning appeared every
   time.
3. **The filter used the wrong names.** When a change *was* detected, the code
   compared settings keys (`live_toggle_hotkey`) against the action labels
   `find_hotkey_conflicts()` reports (`Turn Live Check on/off`), so a
   changed-shortcut-only check could never match anything.

## What changed

* `SettingsDialog.accept()` computes which *actions* changed (settings key ->
  action label through one shared `_HOTKEY_LABELS` map) and only asks about
  those. No change, no scan, no dialog - measured at 0.13 ms on this machine
  with the owner's own settings.
* `find_hotkey_conflicts(..., only_labels=…)` filters duplicates, system keys
  and running-app menu matches to the changed actions, so a duplicate between
  two *untouched* shortcuts is not reported and a duplicate that the change
  *creates* still is.
* The stale `_hotkey_conflict_acknowledged` flag is gone: a shortcut that was
  accepted is simply "unchanged" on the next save.

## What the owner sees now

* Opening Settings and pressing Save: no dialog (whatever the shortcuts are).
* Changing a shortcut to something taken (for example `Cmd+Shift+3`, macOS's
  screenshot key, or a combination one of ByteProof's own actions already
  uses): the same warning, once, at that moment, with "Choose Another" or
  "Save Anyway".

## Verified

```
$ QT_QPA_PLATFORM=offscreen venv/bin/python - <<'PY'   # with the owner's settings
live_toggle_hotkey: <cmd>+<shift>+l
Settings built in 325 ms; unchanged Save took 0.13 ms, no conflict scan, no dialog
```

(The scan is replaced by a stub that raises, so the run proves it was not
called.)

New tests:

```
tests/test_hardening.py::test_hotkey_conflicts_can_be_limited_to_the_changed_shortcut
tests/test_hardening.py::test_saving_settings_without_changing_a_shortcut_does_not_warn
tests/test_hardening.py::test_changing_a_shortcut_to_a_conflicting_one_still_warns
```

```
$ BYTEPROOF_CI_PROGRESS=1 ./scripts/run_tests.sh
=== test_hardening.py       222 passed
=== test_license_cycle.py     3 passed
=== test_live_preview.py    164 passed
=== test_smoke.py           113 passed
All test files passed.
```

## Beta

`2.4.1-beta.6`, built, notarised, and installed through the app's own
unattended update (beta.5 -> beta.6, verified end to end again).
