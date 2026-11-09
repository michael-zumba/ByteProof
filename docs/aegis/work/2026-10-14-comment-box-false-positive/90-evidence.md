# "Word's comment box is open" with no comment box — Evidence

## 1. The owner's log, 2026-10-14

```
[09:16:09] AX text-element pid=57970 role=AXScrollArea desc=scroll area
[09:16:09] AX selection_details: editable=True role=AXScrollArea pid=57970
[09:16:09] LIVE SKIP: too_long bundle=com.microsoft.word chars=14376 limit=4000
...
[09:17:41] APP: pill shown (processing): Proofreading…
[09:17:41] APP: pill shown (warning): Skipped: Word's comment box is open.
           Finish or post the comm…
[09:17:59] APP: pill shown (processing): Proofreading…
[09:17:59] APP: pill shown (success): Proofreading…
```

The skip is the only one in the whole log entry for the session, and the
immediate retry ran - the refusal followed a transient focus state, not a
comment box the owner could see (no `LIVE WORD COMMENT BOX` line appears
anywhere in the session).

## 2. What Word can report as a focused text element

An Accessibility walk of the open document window (Word 16.x, this machine,
2026-10-14) found eight elements whose role is in `TEXT_FIELD_ROLES`:

```
AXTextArea  'Page 1 content' ... 'Page 5 content'   (the document's pages)
AXComboBox  'Font:'
AXComboBox  'Font Size:'
AXTextField 'Search (Cmd + Ctrl + U)'
```

Only a composer carries a "Post comment" button - no such node existed in
the window at rest. So `focused_text_field(...) is not None` was true for
ordinary document and ribbon surfaces, and the manual proofread refused them
all with the comment-box message.

## 3. The change, exercised

- `src/logic.py`: the stand-down is gated on
  `focused_text_field(...)["is_comment_box"]`; every probe answer is logged
  as `MANUAL PROOFREAD: Word keyboard in comment composer|other text field
  (role=… desc=…)` so the surface behind any future refusal is named in
  `capture.log`.
- `src/word_integration.py`: `focused_text_field` also reports the element's
  description, and a focused element whose ancestors include a comment
  thread card ("N of M, Comment thread from …") is classified as a comment
  box too - covering a reply composer whose button is not titled "Post
  comment".
- Tests (fail against the old blanket refusal):
  `test_a_manual_proofread_runs_with_word_keyboard_in_other_fields`,
  `test_a_reply_composer_is_recognised_by_its_thread_card`;
  `test_a_manual_proofread_leaves_an_open_comment_box_alone` still passes.

```
$ ./scripts/run_tests.sh
=== test_hardening.py    224 passed
=== test_license_cycle.py  3 passed
=== test_live_preview.py 164 passed
=== test_smoke.py        113 passed
All test files passed.

$ venv/bin/ruff check src/logic.py src/word_integration.py tests/test_hardening.py
All checks passed!
```

## 4. Beta built and installed

```
$ venv/bin/python tools/bump_version.py 2.4.1-beta.7
$ venv/bin/python scripts/check_version.py
version markers agree: 2.4.1-beta.7
$ ./build_macos.sh            # signed, notarised, stapled
$ ./scripts/install_local_beta.sh dist/ByteProof.app
Installed ByteProof 2.4.1-beta.7 (signature verified).
ByteProof is running. Its Accessibility permission was left in place.
```

## 5. Open

- The owner's own next hotkey press is the end-to-end confirmation. If the
  message ever appears again, the new `MANUAL PROOFREAD:` line names the
  surface that caused it (composer vs other field), which closes the loop
  either way.
