# Live edits in the Codex composer, and the hover that comes back — Checkpoint

## What the log showed (owner's 14:15 session, app 'ChatGPT')

`capture.log`, the 2026-09-30 session in the Codex/ChatGPT composer:

```
[14:15:15] LIVE APPLY ALL: app='ChatGPT' has_range=True count=4
[14:15:16] ax_replace_range: the app put the text at 574 instead of 570
[14:15:17] ax_replace_range: the app put the text at 544 instead of 540
[14:15:18] ax_replace_range: the app put the text at 478 instead of 474
[14:15:19] ax_replace_range: the app put the text at 331 instead of 327
[14:15:16] LIVE APPLY ALL: the write is present despite the failure report
...
[14:15:39] LIVE DONE SYNC FAIL: selection changed: previewed=<len=146 …>
[14:15:41] LIVE DONE SYNC FAIL: selection changed: previewed=<len=65 …>
[14:15:42] LIVE DONE SYNC FAIL: selection changed: previewed=<len=29 …>
```

An earlier session in the same app shows the same pattern at +3 (312 for a
span at 309).

## Root causes

### 1. The composer pastes a few code points late, and the batch accepted it

Every write landed 3–4 code points to the right of the span it was asked to
replace: the head of the original span stayed in front of the new text and the
same number of characters after it were swallowed — "some words are not
properly ordered". Two gaps let the whole batch go through anyway:

* `_write_evidence` asked only whether the new words existed *near* the span
  (`_locate_span`), so a landing beside the span read as "applied".
* `ax_replace_range` reported the late landing and gave up, but the batch kept
  writing into a field whose offsets it could no longer trust.

### 2. After the apply, the panel was anchored to a stale selection

The composer reports its selection asynchronously. `_end_apply` read once,
adopted whatever came back (in the log: one of the old sub-ranges) and marked
it as already previewed — so the three following previews each read a
different stale selection and died with `SYNC FAIL`. A preview dropped that
way also left the selection marked "seen" until it was reselected.

## What changed

| Area | Change |
| --- | --- |
| `generic_editing.py` | `ax_replace_range` captures the field before the paste and, when the new text sits a few code points late with the leftover matching the head of the original span (and the rest of the field provably untouched), repairs the whole affected region with one compensated write, verified. `MAX_LATE_LANDING = 8`. |
| `generic_editing.py` | An unexplained landing writes nothing further; the app keeps its selection back and the caller is told. |
| `live_service.py` | `_write_evidence` is positional: "kept" and "applied" both require the text at the span itself; anything else stops the batch. A replacement that is a prefix of the original (quick → quickly) is disambiguated by reconstructing the original tail. |
| `live_service.py` | `_apply_result_text` records what each apply wrote; `_end_apply` re-reads the selection with a settle (a read matching the applied text wins) before anchoring, and leaves the edited text previewable instead of marked "seen". Word keeps its single AppleScript read. |
| `live_service.py` | `_sample` re-arms the trigger when the pointer leaves a previewed selection and comes back to it: the panel returns from the cache, with no second provider call. |

## Deliberately unchanged

- The pointer gate itself, the debounce, and the "don't preview a selection
  the user made mid-apply" rule (kept for any state that is not the applied
  text).
- Word's single read per apply: the settle loop only runs for AX apps.
- No public feed, no version bump beyond the beta.

## Tests (written first, watched fail)

```
tests/test_live_preview.py::test_ax_replace_range_repairs_a_late_landing
tests/test_live_preview.py::test_ax_replace_range_does_not_repair_an_unexplained_landing
tests/test_live_preview.py::test_two_drifting_writes_land_exactly_in_reverse_order
tests/test_live_preview.py::test_write_evidence_requires_the_edit_at_the_span_itself
tests/test_live_preview.py::test_end_apply_re_anchors_to_the_apps_settled_selection
tests/test_live_preview.py::test_hover_after_apply_previews_the_edited_text
tests/test_hardening.py::test_hovering_the_text_again_re_arms_a_dropped_preview
```

The failing runs: the two repair tests failed with `ok is False` against the
old code; the evidence test returned `"kept"` where `"unknown"` is correct;
the re-arm test found `_pending == []` after the pointer returned; the settle
test anchored to the stale text.
