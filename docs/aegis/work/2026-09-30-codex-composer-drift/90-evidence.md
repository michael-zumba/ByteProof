# Live edits in the Codex composer, and the hover that comes back — Evidence

## 1. The defect, from the owner's log

```
[14:15:15] LIVE APPLY ALL: app='ChatGPT' has_range=True count=4
[14:15:16] ax_replace_range: the app put the text at 574 instead of 570
[14:15:17] ax_replace_range: the app put the text at 544 instead of 540
[14:15:18] ax_replace_range: the app put the text at 478 instead of 474
[14:15:19] ax_replace_range: the app put the text at 331 instead of 327
[14:15:16] LIVE APPLY ALL: the write is present despite the failure report
[14:15:17] LIVE APPLY ALL: the write is present despite the failure report
[14:15:18] LIVE APPLY ALL: the write is present despite the failure report
[14:15:19] LIVE APPLY ALL: the write is present despite the failure report
[14:15:39] LIVE DONE SYNC FAIL: selection changed: previewed=<len=146 sha=a8e90bbe> now=<len=65 sha=fc1d491a>
[14:15:41] LIVE DONE SYNC FAIL: selection changed: previewed=<len=65 sha=fc1d491a> now=<len=29 sha=1e35fb21>
[14:15:42] LIVE DONE SYNC FAIL: selection changed: previewed=<len=29 sha=1e35fb21> now=<len=60 sha=f95817fa>
```

An earlier session in the same app shows the same late landing at +3
(`put the text at 312 instead of 309`).

## 2. Tests first (red), then green

New tests, watched failing against the old code:

```
$ QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_live_preview.py -q -k "late_landing or unexplained_landing or reverse_order"
2 failed, 1 passed
    test_ax_replace_range_repairs_a_late_landing            -> ok is False
    test_two_drifting_writes_land_exactly_in_reverse_order  -> ok_first True and ok_second False

$ … -k "write_evidence_requires"
assert 'kept' == 'unknown'    (a drifted landing read as "kept" — the batch
                               would have carried on)

$ … -k "settled_selection or hover_after_apply"
2 failed
    the stale first read was adopted; previewed == []

$ … tests/test_hardening.py -k "re_arms"
assert ([]) == ...   (after the pointer returned, _pending was empty)
```

After the implementation, the full gate on the beta commit:

```
$ venv/bin/python scripts/check_version.py
version markers agree: 2.2.3-beta.1 (tuple (2, 2, 3, 1))
$ venv/bin/python -m ruff check src tests
All checks passed!
$ BYTEPROOF_CI_PROGRESS=1 QT_QPA_PLATFORM=offscreen \
      venv/bin/python scripts/run_tests_ci.py tests/test_*.py
tests/test_live_preview.py   155 passed
tests/test_hardening.py      195 passed
tests/test_smoke.py          110 passed
All test files passed.
```

## 3. The repair, against the model the log describes

`_FakeAXField` now takes `paste_drift`: the range write is confirmed, the
requested text is clamped, but the paste lands `drift` code points to the
right — exactly the 574-for-570 behaviour. With `paste_drift=4`:

```
field       "the quick brown fox"
span        4..9  "quick"  ->  "quickly"
drifted     "the quicquicklywn fox"          (the original defect)
repaired    "the quickly brown fox"          ok=True, "Applied."
```

A landing that cannot be explained (drift 40, or a tail that does not match)
gets no second write: one paste, `ok=False`, and the batch stops.

## 4. Build and install record

```
$ ./build_macos.sh arm64
notarytool: id 4b3cd01e-7f8e-45d0-98e0-9682054282af  status: Accepted
stapler: The staple and validate action worked!
Build complete! Installer: ByteProof_Installer_AppleSilicon.dmg
sha256 60d26eeea6a0e2692f574b84aa5f750531b62af4e82a1121c34cd81ae284f2ce

2.2.2 (installed) -> previous-versions/ByteProof_2.2.2.app
$ ditto dist/ByteProof.app /Applications/ByteProof.app
$ defaults read /Applications/ByteProof.app/Contents/Info.plist CFBundleShortVersionString
2.2.3-beta.1
$ codesign --verify --deep --strict /Applications/ByteProof.app
INSTALLED_SIGNATURE_OK
$ spctl -a -vvv -t exec dist/ByteProof.app
accepted  source=Notarized Developer ID
$ open -a /Applications/ByteProof.app      # running
capture.log: [14:50:28] LIVE PERMISSION: trusted=True app='ByteProof'
```

The public update feed is untouched (2.2.2 is still the released version).

## 5. Limits and follow-ups

* The live probe in the owner's Codex/ChatGPT composer was **not** run: that
  window is the owner's own chat composer and they were using it at the time.
  The repair is driven by what the field actually holds after the write
  (leftover, insert, tail all proven), not by a theory of why the app drifts,
  and it refuses anything it cannot prove; the installed beta is the real
  test.
* The settle read costs up to ~0.24 s inside an apply on AX apps, and one
  extra provider call only when the pointer stays with the edited text after
  an apply (the cache answers when the text was already previewed).
* `MAX_LATE_LANDING = 8`: a drift larger than that is treated as an
  unexplained landing (stop and report), not repaired.
