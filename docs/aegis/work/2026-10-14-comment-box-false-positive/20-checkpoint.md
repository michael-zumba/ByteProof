# "Word's comment box is open" with no comment box — Checkpoint

## What the log showed

`capture.log`, the owner's 2026-10-14 session:

```
[09:17:41] APP: pill shown (processing): Proofreading…
[09:17:41] APP: pill shown (warning): Skipped: Word's comment box is open.
           Finish or post the comm…
```

The press after it (09:17:59) ran normally, so the surface that triggered the
refusal came and went with the user's focus. Earlier in the same session
(09:16:09–09:16:10) the live poll had used its Accessibility path for Word
while the owner was plainly working in the manuscript (a 14,376-character
selection was read and refused as too long), which is the same detection
signal from the other side.

## What was wrong

`proofread_selection_once` asked Word for its focused Accessibility element
and stood down whenever the answer was *any* text element:

```python
if word_pid and focused_text_field(word_pid) is not None:
    return COMMENT_BOX_SKIPPED_STATUS, ...
```

`focused_text_field` reports every Word text surface: the comment composer,
the status-bar search box ("Search (Cmd + Ctrl + U)"), the ribbon's Font and
Font Size boxes, and the document pages' own content areas (measured
2026-10-14: an Accessibility walk of the open document window found eight
text-field elements, of which only a composer carries the "Post comment"
button). The comment-box message - and the refusal - fired for all of them.
The Oct-8 contract only ever needed the composer: it is the one surface whose
draft the AppleScript read closes.

## What changed

1. **The refusal is gated on the composer.** The manual proofread stands down
   only when `focused_text_field(...)` answers `is_comment_box: True` - the
   same classifier the live path has used since 2.4.1-beta.6. Any other
   focused Word text surface lets the flow read the manuscript selection,
   which is what the hotkey was pressed for.
2. **Reply composers are still caught.** The primary composer recognition is
   Word's own "Post comment" button; a reply composer may label it
   differently, so the focused element's own ancestors are also checked for
   the thread card ("N of M, Comment thread from <author>"). Document text
   areas never carry that ancestor.
3. **The surface is logged.** Every manual proofread names the surface the
   keyboard was in
   (`MANUAL PROOFREAD: Word keyboard in comment composer|other text field
   (role=… desc=…)`), so the next mis-detection is diagnosable from
   `capture.log` alone instead of a bare skip message.

## Tests

- `test_a_manual_proofread_runs_with_word_keyboard_in_other_fields`: the
  search box, a page-content area and the font-size box each let the flow
  proceed (fails against the old blanket refusal).
- `test_a_reply_composer_is_recognised_by_its_thread_card`: a composer
  without a Post button is still classified as a comment box.
- `test_a_manual_proofread_leaves_an_open_comment_box_alone` (Oct-8) still
  passes unchanged.

## What the owner should watch

1. Press the proofread hotkey while Word's search box or the ribbon has the
   keyboard (after selecting text in the document): the proofread should run
   on the selection, not report a comment box.
2. Type in a comment box and press the hotkey: it must still stand down and
   must not disturb the draft.
