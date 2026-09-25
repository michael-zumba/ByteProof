# Log hygiene and paste verification — Checkpoint

## What the log showed

`capture.log` (5,690 lines, 323 KB) and `debug_hotkeys.log` (289,186 bytes,
written at 13:59) were read in full. Five defects, in the order they matter:

1. **Every keystroke was written to disk in plain text.** `src/hotkeys.py`
   logged `Key down: <char> flags=…` from the macOS global event monitor, with
   no debug gate. The file held typing from every application, including text
   typed into other apps' prompt boxes, and had shipped that way since 1.0.0.
2. **One repeated line owned two thirds of the support log.** 4,149 of 5,690
   lines (217 KB of 323 KB) were the same `LIVE SKIP: too_short`, written once
   per 350 ms poll tick: 21 minutes for Chrome for Testing, 29 minutes for
   TextEdit. The log was rolling itself over and discarding real evidence.
3. **A landed apply was reported as a failure.** 13:52:16 and 13:52:27 both
   showed `mac_replace: paste was not observed in the target` and the pill
   "Could not confirm the paste — please check the document." One second after
   the second report, the app's own sync found the document no longer held the
   previewed text at the range, and a copy of the selection came back empty -
   the signature of a paste that had landed and collapsed the caret.
4. **The live preview and the apply fought over the clipboard.** At 13:52:26
   and 13:52:27, inside the apply's paste window, the still-running live sync
   posted two Command-Cs. A copy empties the pasteboard before pressing the
   key, so the paste was reading a pasteboard the app had just cleared.
5. **A redaction hole.** `_verify_full_apply` logged the first 40 characters of
   the user's document text (`got=… want=…`) into `capture.log`, against the
   rule the same module documents and a test enforces.

## Root causes and fixes

### 1. Keystroke logging (privacy)

`hotkeys.log_key_event()` now writes only when `BYTEPROOF_DEBUG_HOTKEYS` is set
to a truthy value; matched-hotkey and startup diagnostics still go through
`log_debug()`. `drop_recorded_keystrokes()` removes the file left by older
builds when key logging is off, and `HotkeyManager.start()` calls it on macOS.

### 2. Skip-line spam (log hygiene)

`_log_skip_once()` mirrors the existing `_log_read_only_once` / `_log_too_long_once`
helpers, keyed by bundle and decision. `too_short` was the one skip that
bypassed them. `pointer_away` and `read_only` were already throttled, which is
why the log held one of each and 4,149 of the other.

### 3. A landed paste read as "not observed" (correctness)

`_wait_for_paste_consumed()` demanded a verbatim substring of the pasted text
in the document. Outlook and other WebKit surfaces store `\n` as `\r`/`\r\n`,
which the rest of the module already normalises (`_same_text`,
`_range_write_candidates`); this one check did not. The containment test now
compares `normalize_line_endings(...)` on both sides, so a landed paste is
recognised while a genuinely different document still reads as `mismatch`.

### 4. Clipboard race (correctness)

Two changes, because either one alone leaves a window:

* The apply holds the live service for its whole lifetime, not just until the
  proofread returns (`_apply_generic_text` holds, `_on_generic_apply_done`
  releases).
* `task_finished()` no longer releases that hold while an apply is running.
  Auto-apply starts from the result handler, which Qt delivers *before*
  `finished` in the same event-loop pass, so the old order put the preview back
  on the clipboard mid-paste.
* `_read_selection_by_copy()` returns nothing while a manual task owns the
  selection, so a preview already in flight cannot post Command-C either.

### 5. Redaction hole

`_verify_full_apply` now logs `_redact(got)` / `_redact(corrected)` (length plus
digest), which is what the rest of `capture.log` uses.

## Verified in the running build

2.2.2-beta.4 started with Accessibility already trusted, and the keystroke log
was dropped on launch: 289,186 bytes before, 538 bytes after, with zero
`Key down` lines. The file now holds only startup and hotkey diagnostics.

## Still open (owner-visible)

* The owner's next Outlook apply is the real confirmation for items 3 and 4.
  What to watch: no "Could not confirm the paste" for a document that did
  change, and no `copy attempt` line inside an apply's window.
* `LIVE SKIP: too_short` should now appear once per app per run.
* Two observations from the same log that are not defects: Outlook always
  falls back from the Accessibility text write to the paste path (six spans on
  13:55, all verified ok afterwards), and the "editable" probe accepts a
  settable non-text element such as an `AXSplitter` (pid 930 in the log). Both
  cost work and log noise rather than correctness; neither was in scope here.
