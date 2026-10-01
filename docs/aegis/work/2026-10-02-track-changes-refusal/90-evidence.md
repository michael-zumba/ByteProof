# A refused Track-Changes write must not stop a proofread — Evidence

## 1. The defect, from the owner's log

DeepSeek was never down: capture.log shows live calls reaching it
(`LIVE DONE: edits=0 provider_ms=4196 provider=DeepSeek`, 10:07:38) and the
in-app connection test passes. The last action of the session is the failure:

```
[10:21:49] APP: pill shown (processing): Proofreading…
[10:21:50] WORD: AppleScript error: 45:111: execution error: Microsoft Word got an error: Can’t set track revisions of active document to false. (-10006)
[10:21:50] WORD: AppleScript error: 67:80: syntax error: A identifier can’t go after this identifier. (-2740)
[10:21:50] APP: pill shown (error): Error: Unable to disable Track Changes in Microsoft Word.
```

Track Changes was already off, and the app still asked Word to set it off.
It then fell back to a property Word's AppleScript dictionary does not have.
Both defects are live-reproducible against Word on this machine:

```
$ printf 'tell application "Microsoft Word"\n    set track changes of active document to false\nend tell\n' | osascript -
42:55: syntax error: A identifier can't go after this identifier. (-2740)
exit=1
$ printf 'tell application "Microsoft Word"\n    set track revisions of active document to false\nend tell\n' | osascript -
exit=0
$ osascript -e 'tell application "Microsoft Word" to get track revisions of active document'
false
```

## 2. Tests first (red), then green

New hardening tests, watched failing against the old code:

```
$ QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_hardening.py -q \
      -k "track_changes_off_is_not_written or refused_redundant or refusal_reaches or property_word_has"
4 failed, 195 deselected
```

After the repair:

```
$ … -k "track_changes"
5 passed, 194 deselected
```

## 3. The repair, against the log the owner saw

`MacOSWordIntegration` now reads the setting before writing it, so a document
that already sits where the proofread needs it gets no write at all — the
call Word refused with -10006. A refusal that does not leave the state wrong
is tolerated; one that does stops with the next step in the message. The
fallback that used `track changes of active document` is removed
(`track revisions` is the property Word has). The gate on the beta commit:

```
$ venv/bin/python scripts/check_version.py
version markers agree: 2.2.3-beta.2 (tuple (2, 2, 3, 2))
$ venv/bin/python -m ruff check src tests
All checks passed!
$ BYTEPROOF_CI_PROGRESS=1 QT_QPA_PLATFORM=offscreen \
      venv/bin/python scripts/run_tests_ci.py tests/test_*.py
All test files passed.   (464 collected across the three files)
```

## 4. Build and install record

```
$ ./build_macos.sh arm64
PyInstaller bundle + stable code signature + Developer ID re-sign: OK
notarytool: Error: HTTP status code: 403. A required agreement is missing
or has expired.            # owner must accept the Apple Developer agreement
                           # in App Store Connect; script stops before create-dmg,
                           # so no ByteProof_Installer_AppleSilicon.dmg this time

2.2.3-beta.1 (installed) -> previous-versions/ByteProof_2.2.3-beta.1.app
$ ditto dist/ByteProof.app /Applications/ByteProof.app
$ /usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" /Applications/ByteProof.app/Contents/Info.plist
2.2.3-beta.2
$ codesign --verify --deep --strict /Applications/ByteProof.app
INSTALLED_SIGNATURE_OK
$ open -a /Applications/ByteProof.app     # running, pid 74460
settings.json: last_run_version 2.2.3-beta.2, provider DeepSeek, track_changes false
```

Repair probe against the owner's live Word session (10:34:21): Word was
running with no document open, so the new code logged
`No active Word document is open (-2700)` from the write attempt and raised
"Microsoft Word would not turn Track Changes off…" instead of pretending to
succeed. That line in capture.log is the probe, not the app.

## 5. Limits and follow-ups

* The exact Word state behind the 10:21:50 refusal was not reproduced (Word
  now accepts the same set and the document was closed by 10:34). The repair
  is grounded in the two proven defects — the redundant write that aborted
  the proofread, and the invalid fallback — not in a theory of the state.
* The installed beta is the real test: select text in Word, press the
  proofread hotkey, and confirm the pill shows the edits landing.
* Notarization and the public DMG stay blocked until the owner accepts the
  Apple Developer agreement; the update feed keeps advertising 2.2.2.
