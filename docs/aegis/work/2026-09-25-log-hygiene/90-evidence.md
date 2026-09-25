# Log hygiene and paste verification — Evidence

## 1. The log, quoted

Five lines carry the whole session. `capture.log`, 2026-09-25:

```
[13:52:16] mac_replace: paste was not observed in the target; reporting for review
[13:52:16] APP: pill shown (error): Could not confirm the paste — please check the document.
[13:52:26] copy attempt 'process' -> <len=0 sha=da39a3ee>
[13:52:27] copy attempt 'system' -> <len=0 sha=da39a3ee>
[13:52:27] LIVE SYNC: document text at the range differs
[13:52:27] LIVE DONE SYNC FAIL: selection changed: previewed=<len=1237 …> now=<len=1237 …>
[13:52:27] mac_replace: paste was not observed in the target; reporting for review
```

The copy at :26–:27 is the live preview's own sync read, landing *inside* the
apply's paste window. The empty results and the changed document at the range
are the signature of a paste that landed.

The two log-hygiene numbers, measured with `wc`/`grep` on the file itself:

```
capture.log            5,690 lines   323,558 bytes
"LIVE SKIP: too_short" 4,149 lines   217,082 bytes   (67%)
  20:16:41 -> 20:38:14  1,298 lines   app='Google Chrome for Testing'
  21:01:16 -> 21:30:00  2,844 lines   app='TextEdit'

debug_hotkeys.log        289,186 bytes, written 13:59
  every line a plain-text "Key down: <char> flags=…", no timestamps
```

## 2. The paste check, before and after

A direct probe of the shipped function (monkeypatched readers, no keystrokes):

```
                                       before the fix      after the fix
landed, document stores LF                 ok                  ok
landed, document stores CRLF             mismatch              ok
landed, document stores CR               mismatch              ok
did not land (genuinely different)       mismatch            mismatch
```

Before:

```
$ venv/bin/python - <<'PY'
from src import generic_editing as ge
new_text = "Dear Sam,\n\nHere is the draft.\n\nRegards,\nAlex"
document = "Hi team,\r\n" + new_text.replace("\n", "\r\n") + "\r\nBye"
ge.GenericTextEditor._mac_ax_selection = staticmethod(lambda pid: "")
ge._mac_ax_field_value = lambda AS, pid: document
print(ge._wait_for_paste_consumed(None, {"pid": 1}, new_text, timeout=0.05))
PY
mismatch
```

This is the failure the owner saw twice: Outlook stores the paragraph breaks as
`\r\n`, the verbatim containment test read that as "the paste was not
observed", and the pill told them to check a document that had already been
written.

## 3. Tests, run red first

New tests, each watched failing before its fix:

| test | failed with |
|------|-------------|
| `test_a_short_selection_is_logged_once_not_on_every_tick` | `assert 6 == 1` skip lines for six ticks |
| `test_the_hotkey_log_keeps_no_keystrokes` | `AttributeError` (no opt-in path existed) |
| `test_key_logging_is_available_when_a_developer_asks_for_it` | same |
| `test_keystrokes_recorded_by_an_older_build_are_dropped` | same |
| `test_keystroke_logging_keeps_the_file_when_a_developer_asked_for_it` | same |
| `test_a_paste_that_landed_reads_back_with_outlooks_line_endings` | `assert 'mismatch' == 'ok'` |
| `test_a_paste_that_did_not_land_is_still_a_mismatch` | (guard: green before and after) |
| `test_a_live_copy_stands_down_while_a_manual_task_owns_the_selection` | copy posted while held |
| `test_the_full_apply_mismatch_keeps_the_document_text_out_of_the_log` | the document text was in `capture.log` |
| `test_an_apply_holds_the_live_preview_until_the_paste_is_done` | `assert ['start'] == ['hold', 'start']` |
| `test_finishing_the_proofread_leaves_the_hold_under_a_running_apply` | `['hold','start','release']` before the apply ended |

The last one is the ordering defect: Qt delivers the worker's `result` before
its `finished`, so auto-apply started the write and `task_finished` released
the hold in the same pass.

## 4. Suite, lint, version

```
tests/test_hardening.py      182 passed
tests/test_live_preview.py   146 passed
tests/test_smoke.py          110 passed
All test files passed.       (scripts/run_tests_ci.py, macOS)
ruff check src tests         All checks passed!
scripts/check_version.py     version markers agree: 2.2.2-beta.4
```

## 5. Shipped

Beta **2.2.2-beta.4** (every change ships as a beta first; the public feed was
left alone by `tools/bump_version.py` because the version is a pre-release):

```
ByteProof_Installer_AppleSilicon.dmg
sha256 fa0a6a46947b8674d1d81242504b238bc15966652454417338b137f526dae7da
stapler validate: The validate action worked!
spctl -a -vvv -t install: accepted, source=Notarized Developer ID
origin=Developer ID Application: YUQIAN ZHANG (9AMNWJRC93)
```

Installed to `/Applications`; 2.2.2-beta.3 was quit and moved to
`~/.Trash/ByteProof-2.2.2-beta.3.app` (recoverable). The new build started as
2.2.2-beta.4 with Accessibility still trusted (`LIVE PERMISSION: trusted=True
app='ByteProof'` in the log), so the owner has no permission to re-grant.

## 6. What the owner can watch in the log

* `debug_hotkeys.log` stays a few hundred bytes with no `Key down` lines; set
  `BYTEPROOF_DEBUG_HOTKEYS=1` in the environment to get them back for a
  debugging session.
* `LIVE SKIP: too_short` appears once per app, not once per tick.
* An Outlook apply that lands reports success: no "Could not confirm the
  paste" together with a document that visibly changed.
* No `copy attempt …` line between an apply's `Applying to …` pill and its
  result.
