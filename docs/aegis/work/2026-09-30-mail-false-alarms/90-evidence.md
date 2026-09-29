# Mail false alarms — Evidence

## 1. The log, quoted

`capture.log`, owner session 2026-09-29 (newest record at the time of the
report), Apple Mail pid 928:

```
[21:01:24] LIVE CLIPBOARD: read after a selection gesture
[21:01:25] copy selection used the app's Copy menu command
[21:01:25] LIVE CLIPBOARD READ: app='Mail' chars=342
[21:01:25] copy selection skipped: rate limited
[21:01:25] EMPTY SELECTION: app='Mail' pid=928 activate_target=False
[21:01:25] APP: pill shown (error): No text selected in Mail.
[21:01:29] APP: pill shown (processing): Polishing email draft from Mail…
[21:01:30] APP: pill shown (processing): Applying to Mail…
[21:01:32] mac_replace: paste was not observed in the target; reporting for review
[21:01:32] APP: pill shown (error): Could not confirm the paste — please check the document.
[21:01:40] LIVE CLIPBOARD READ: app='Mail' chars=345
[21:01:40] copy selection skipped: rate limited
[21:01:40] EMPTY SELECTION: app='Mail' pid=928 activate_target=False
[21:01:40] APP: pill shown (error): No text selected in Mail.
[21:01:47] mac_replace: paste was not observed in the target; reporting for review
[21:01:47] APP: pill shown (error): Could not confirm the paste — please check the document.
```

The refusal (`rate limited`) sits between a successful copy read and the
empty-selection report, and the two "not observed" reports are followed by a
read of the same selection that is 3 characters longer (342 -> 345) — the
polished text had landed. The same signature earlier the same day, Outlook:

```
[12:08:55] LIVE PREVIEW: app='Microsoft Outlook' chars=1062
[12:09:00] mac_replace: paste was not observed in the target; reporting for review
[12:09:00] APP: pill shown (error): Could not confirm the paste — please check the document.
[12:09:03] LIVE PREVIEW: app='Microsoft Outlook' chars=1081
```

All five pills that day were false alarms; there were no tracebacks
(`error.log`'s newest entry is 2026-09-10).

## 2. Failing tests first (RED)

Run before any production code changed:

```
$ venv/bin/python -m pytest tests/test_hardening.py -k "manual_read or proofread_button" -x -q
E   AttributeError: <module 'src.generic_editing' ...> has no attribute 'copy_read_refused'
FAILED tests/test_hardening.py::test_a_manual_read_refused_by_the_copy_rate_limit_is_retried

$ venv/bin/python -m pytest tests/test_hardening.py -k "paste_copy_check or copy_of_the or paste_proved or paste_without_copy" -q
FAILED test_a_copy_of_the_pasted_text_proves_the_paste
FAILED test_the_paste_copy_check_never_reads_another_apps_selection
FAILED test_the_paste_copy_check_spares_apps_with_trustworthy_accessibility
FAILED test_the_paste_copy_check_waits_out_the_copy_rate_limit
FAILED test_a_paste_proved_by_a_real_copy_is_not_reported_as_unconfirmed
FAILED test_a_paste_without_copy_evidence_keeps_its_failure_report
6 failed, 188 deselected
```

## 3. Behavior the fix asserts

Manual read (`logic.polish_selection_once`, hotkey/pill path):

```
read refused, retry answered        -> the retry's text is used (no message)
read refused, retry refused         -> "Could not read the selection in Mail — please try again."
read answered empty (menu disabled) -> "No text selected in Mail."   (unchanged)
```

Proofread button (`ProofreaderApp._read_button_target_selection`): the same
retry; a refused read cancels with "Could not read the selection in Mail —
please try again." instead of blaming the selection.

Paste evidence (`copy_verified_paste`, read-only, never pastes again):

```
Mail/Outlook/Pages, copy returns the pasted text   -> proves the write (True)
same apps, copy returns other text                 -> stays a failure (False)
same apps, copy refused by the rate limit          -> one bounded retry
any other app (AX is trustworthy)                  -> no keystroke spent
target not frontmost                               -> refused (a copy would
                                                      read another app)
```

`GenericApplyWorker` upgrades to the success toast only on that proof, and a
copy proof outranks the weaker AX-only follow-up read so a stale AX answer
cannot re-fail a proved write.

## 4. The gate

```
$ venv/bin/python -m ruff check src tests
All checks passed!
$ venv/bin/python scripts/check_version.py
version markers agree: 2.2.2-beta.5 (tuple (2, 2, 2, 5))
$ BYTEPROOF_CI_PROGRESS=1 venv/bin/python scripts/run_tests_ci.py tests/test_*.py
============================= 194 passed in 40.16s =============================
--- tests/test_hardening.py finished in 40.3s
============================= 146 passed in 2.48s ==============================
--- tests/test_live_preview.py finished in 2.7s
============================= 110 passed in 11.16s =============================
--- tests/test_smoke.py finished in 11.3s
All test files passed.
```

`tests/test_hardening.py` went from 182 to 194 tests (12 new, 2 red runs
recorded above).

## 5. Build and install record

```
$ ./build_macos.sh
Build complete! Installer: ByteProof_Installer_AppleSilicon.dmg
Architecture: arm64
notarytool: id 3db94ab5-71ab-4972-aef0-aceb5351ea3d status: Accepted
stapler: The staple and validate action worked!

2.2.2-beta.4 (installed)  -> previous-versions/ByteProof_2.2.2-beta.4.app
$ ditto dist/ByteProof.app /Applications/ByteProof.app
$ defaults read /Applications/ByteProof.app/Contents/Info.plist CFBundleShortVersionString
2.2.2-beta.5
$ codesign --verify --deep --strict /Applications/ByteProof.app
signature ok
$ open -a /Applications/ByteProof.app     # running re-test build
"LIVE PERMISSION: trusted=True app='ByteProof'"
```

The public update feed is untouched (pre-release bump skips it by design).

## 6. Follow-ups for the next session

* Owner re-test in Mail and Outlook: no "No text selected" right after a
  selection, and no "Could not confirm the paste" for a draft that did
  change. If the copy shows the original text is still selected, the manual
  path reports the failure and does not retry — the live apply is the one
  that retries.
* Watch the log for the new diagnostics: `refused=True` on the EMPTY
  SELECTION line, `APPLY: ... a copy of the selection proves it landed`, and
  `copy selection skipped: rate limited` should no longer precede a
  "No text selected" pill.
