# Mail false alarms — Checkpoint

## What the log showed

`capture.log`, the owner's 2026-09-29 session (the newest record at the time of
the report), Apple Mail at 21:01. Four red pills in twenty-two seconds, two
repeating cycles:

```
[21:01:25] LIVE CLIPBOARD READ: app='Mail' chars=342
[21:01:25] copy selection skipped: rate limited
[21:01:25] EMPTY SELECTION: app='Mail' pid=928 activate_target=False
[21:01:25] APP: pill shown (error): No text selected in Mail.
[21:01:32] mac_replace: paste was not observed in the target; reporting for review
[21:01:32] APP: pill shown (error): Could not confirm the paste — please check the document.
... the pair repeats at 21:01:40 (chars=345) and 21:01:47 ...
```

Two more of the same signature earlier the same day, in Outlook
(`[12:09:00]`, next read 1062 -> 1081 chars). No tracebacks; the errors in
`error.log` are from 2026-09-05..10.

## Root causes and fixes

### 1. "No text selected in Mail." (false)

Mail's compose window exposes no Accessibility selection, so both the live
preview and the manual proofread read it with a real Command-C
(`_mac_copy_selection`). That read is rate-limited to one per 0.5 s
(`MIN_COPY_INTERVAL_S`, added in 2.0.2-beta.9 to stop a Mail keystroke storm)
and shares one budget across callers. When the live preview read the selection
and the owner pressed the hotkey inside the same half-second, the manual read
was refused, returned `""`, and `polish_selection_once` reported it as an
empty selection.

Fix: `_mac_copy_selection` now records refusals (`copy_read_refused()`), and
both manual read sites wait out the window and read once more before saying
anything:

* `logic.polish_selection_once` (hotkey/pill path) — bounded retry; if the
  retry is refused too, the message is "Could not read the selection in
  Mail — please try again." instead of "No text selected".
* `ProofreaderApp._read_button_target_selection` (Proofread-button path) — same
  retry and the same honest message split.

An answered empty read (Copy menu disabled, or an app that copies nothing)
still says "No text selected in ...".

### 2. "Could not confirm the paste" (false)

`_mac_replace` verifies the paste by reading Accessibility text back. Mail's
WebKit composer and the new Outlook answer that read with stale or unrelated
text, so a landed paste reads as `mismatch` and the worker reports it. The
evidence that these pastes landed: the next read of the same selection in the
owner's log is 342 -> 345 chars (Mail, 21:01) and 1062 -> 1081 chars (Outlook,
12:09).

Fix: `copy_verified_paste()` (read-only, never pastes again) — after a failed
`replace_selection`, for the surfaces whose Accessibility reads cannot prove a
write (`CLIPBOARD_FALLBACK_BUNDLE_IDS` plus Outlook), the apply worker takes
one bounded Command-C copy; only an exact match of the pasted text upgrades
the report to success. A copy that shows other text still reports the failure.
The check is skipped when the target is not frontmost (a copy would describe
another app), and the copy-based proof outranks the weaker AX-only follow-up
read so a stale AX answer cannot re-fail a proved write.

## Verified in the running build

2.2.2-beta.5 built, signed, notarized (accepted), and installed to
`/Applications`; the previous beta.4 bundle moved to
`previous-versions/ByteProof_2.2.2-beta.4.app`. The installed app reports
2.2.2-beta.5, passes `codesign --verify --deep --strict`, and started with
Accessibility already trusted (`LIVE PERMISSION: trusted=True`), so the
owner's permission grant survived the update. Details in 90-evidence.md.

## Still open (owner-visible)

* The owner's next Mail and Outlook proofreads are the real confirmation.
  Watch for: no "No text selected" after a selection the live preview has just
  read, and no "Could not confirm the paste" for a draft that did change.
* When a copy proves the manual paste did **not** land (`not_applied`), the
  manual path still reports "Could not confirm the paste" and does not retry;
  only the live apply retries (via System Events). Left as is on purpose: a
  second manual paste is the one thing that can duplicate a paragraph.
* The 2026-09-29 log also showed 58 `LIVE DONE SYNC FAIL` lines across Word,
  Outlook and Safari — the live preview dropping suggestions while the text
  moved during the provider call. Expected noise, not part of this change.
