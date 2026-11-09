# "Word's comment box is open" with no comment box — Task Intent

## The owner's report

"Can you help check the latest log, why it always shows: Skipped: Word's
comment box is open. Finish or post the comment first — Live Check reviews
comment text as you write it, and this flow would close the box. I dont have
the comment open in the word."

## Goal

The proofread hotkey proofreads the selected manuscript text whenever the
keyboard is not actually inside Word's comment composer. The message
"Word's comment box is open" appears only when the composer owns the
keyboard, because that is the one surface whose draft the flow's read would
close.

## Success evidence

- The decision in `proofread_selection_once` is gated on
  `focused_text_field(...)["is_comment_box"]`, the same classifier the live
  path uses, instead of "any Word text element has the keyboard".
- A reply composer without the measured "Post comment" button is still
  classified as a comment box (its thread-card ancestor says so).
- Each manual proofread records which Word surface held the keyboard, so a
  mis-detection is named in `capture.log` instead of blocking silently.
- Tests: a non-comment text field (search box, font box, page content) lets
  the proofread proceed; a reply composer still stands it down.
- `./scripts/run_tests.sh` passes; a beta is built and installed for the
  owner.

## Stop condition

- `done`: suite green, beta installed, owner told what to test.

## Non-goals

- Changing how the live preview routes Word reads; the report is about the
  manual proofread message.
- Publishing a release. Per the release policy this ships as a beta first.
