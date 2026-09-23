# Live edits that keep their place — Checkpoint

## What the log showed

`capture.log` (support folder) was read end to end. Two live-apply outcomes
dominated it:

1. **Word live edits were never verified.** `WORD: live edit read-back
   differs; reporting for review` appears **57 times** and is essentially the
   only Word apply outcome in the file. Every one of those writes reported
   "Applied — please check the document."
2. **ChatGPT live edits were refused and the app's selection was dragged.**
   One Apply All of six suggestions produced six failures, and the four
   previews that followed died with `LIVE DONE SYNC FAIL: selection changed`,
   each reporting a *different* selection (29 → 73 → 36 → 39 characters) — the
   app's own selection was being walked through the spans we had asked it to
   select.

## Root causes

### 1. Word: the read-back read the wrong extent (not a Word problem)

`MacOSWordIntegration.apply_live_edit` wrote with `set content of r` and then
read `content of r` back. Word keeps a range object's **old extent** after a
content write, so the read-back compared the *first N characters* of the new
text with *all* of it: any edit that changed the length mismatched. Probed on a
scratch document (`/tmp/byteproof-word-test/probe2.applescript`):

```
r-before=[quick]                     <- create range d start 4 end 9
fresh=[quickly brown]                <- a fresh range over the written extent
fresh-matches-clipboard=true
doc=[The quickly brown brown fox jumps over the lazy dog.]
```

The write had landed correctly all along; the check that was supposed to prove
it could never pass. The Windows path in the same file already read a fresh
range - the macOS path now does the same, and both sides compare newline form
canonically so Word's `\r` spelling of a break does not read as a mismatch.

### 2. Accessibility apps: the only available write path moves the selection

`AXReplaceRangeWithText` is not available: macOS's public AX API ships
`AXUIElementCopyParameterizedAttributeValue` and **no setter**, the attribute
constant is absent from the SDK, and neither the bundled PyObjC nor the system
Python exposes it (checked). So every apply in a generic app is "set the AX
selection range, then write or paste" - which moves the app's own selection.
Two consequences:

* **A refused write left the selection on our span.** Nothing put it back, so
  the user's place was gone and every following preview compared against the
  wrong selection - exactly the churn in the log.
* **A lagging app could be written into the wrong place.** When the range
  readback lags (ChatGPT does), the code accepted a *selected text* match as
  proof that the range landed. If the span's words also appear elsewhere, a
  selection sitting on that other occurrence reads identically - and the paste
  then replaces words the user never chose. That is "the edit lost its
  position".

### 3. A refused apply kept writing

After a failed write `_apply_all_locked` continued with the remaining spans on
the theory that they lie to the left and cannot have moved. That is true for
*offsets* but not for *unknown state*: when the app had changed the text
without confirming the edit, the loop kept going (six attempts in the log)
while the app's selection moved further from the user's.

## What changed

| Area | Change |
| --- | --- |
| `word_integration.py` | Live edit read-back uses a **fresh range over the written extent**; the before-text guard and the read-back compare newline form canonically (`canon` handler). |
| `generic_editing.py` | `ax_replace_range` snapshots the app's selection before any write and **puts it back** on every path that does not write; the selected-text confirmation is accepted only when the span text has **one home** in the field; a failed paste logs where the text landed if it is readable. |
| `generic_editing.py` | New `ax_select_range` (code points → UTF-16) so the service can re-select the user's text after a verified edit. |
| `live_service.py` | `_apply_one` and a fully applied Apply All re-select the captured text, now carrying the edits, so the panel's remaining suggestions stay anchored and the user's place covers the edited passage; `_apply_all` stops at a write whose outcome the app cannot explain (`_write_evidence`) and keeps the rest of the review on screen; the log says why. |

The dead `AXReplaceRangeWithText` branch is documented as unreachable rather
than left looking like a safety net.

## What was verified

Against real Word documents (scratch documents, closed without saving):

| Case | Result |
| --- | --- |
| 1-character-length change (`quick` → `quickly brown`) | `ok=True "Applied."` (was "please check"); document exactly right |
| Replacement containing a line break | `ok=True "Applied."`; break stored |
| Range holding different text | refused: "The text moved or changed — please try again." (**no write**) |
| Emoji before the span (26 code points, doc span 27) | mapped 13..18 → 14..19; text landed on `gamma` |
| Tracked deletion inside the selection | mapped 6..11 → 6..16; `Alpha gamma delta.` → `Alpha gamut delta.` |

Automated: `tests/test_live_preview.py` 137 → 146 tests. The new tests fail
against the pre-fix code (five verified by reverting each file and running
them). Full suite: 146 + 173 + 108 passed; `ruff` clean.

## Still open (named, not fixed)

* `LIVE DONE SYNC FAIL` still drops a preview when the selection changes
  between the debounce and the provider reply (60 occurrences in the log, most
  of them transient `<len=0>` or ±1-character Word reads). Not a position
  problem: nothing is written, the card simply does not appear.
* Apps that ignore programmatic selection writes (ChatGPT's composer) still
  cannot be edited live; they are now refused honestly, with the selection put
  back, instead of being written into a guessed position.
* A verified apply that an app rewrites on insert (Teams-style) still closes
  the panel (`_sync_after_apply` cannot confirm the corrected selection).
